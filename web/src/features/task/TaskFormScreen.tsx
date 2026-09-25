/**
 * Форма задачи (ЗАД.ФТ.9–10, ЗАД.ФТ.12).
 *
 * Правила, которые нельзя регрессировать:
 *
 * * пустое поле — «ключ не отправляем вовсе» (`toFormData`): `Form(None)` в
 *   FastAPI разбирает пустую строку как значение и отвечает 422;
 * * область всегда уходит файлом, даже при правке без ее изменения, — бэкенд
 *   требует `area_file` и на PUT, поэтому форма держит текущую область и
 *   воспроизводит ее файлом;
 * * `expected_version` при правке: 409 означает, что задачу изменил кто-то
 *   другой, и сообщение бэкенда уже называет его.
 *
 * Ошибки валидации приходят списком `{field, message}` и показываются у
 * своих полей. Окно работ задается в местном времени задачи — поясом по
 * умолчанию берется пояс браузера.
 */
import { useEffect, useMemo, useState } from "react"
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { ApiError, api, fieldIssues, geojsonFile } from "@/api/client"
import type { TaskDetail } from "@/api/client"
import { LegendSlot, Sidebar, useHeaderContext, useStep } from "@/app/AppShell"
import { EnvironmentLayers, layerCounts } from "@/map/EnvironmentLayers"
import { AreaLayer } from "@/map/AreaLayer"
import { ConflictLayer } from "@/map/ConflictLayer"
import { DrawAreaLayer, pointsToPolygon, polygonToPoints } from "@/map/DrawAreaLayer"
import type { DrawPoint } from "@/map/DrawAreaLayer"
import { EnvironmentLegend, LegendBox } from "@/map/Legend"
import { Button } from "@/shared/ui/Button"
import { Banner, SideHead } from "@/shared/ui/Display"
import { Field, Segmented } from "@/shared/ui/Field"
import { FilePicker } from "@/shared/ui/FilePicker"
import { Icon } from "@/shared/ui/Icon"
import { Skeleton } from "@/shared/ui/States"
import { useToast } from "@/shared/ui/Toasts"
import { browserTimeZone, zoneLabel } from "@/shared/format"
import { useToggleSet } from "@/shared/useToggleSet"
import { STEP } from "./useTaskFlow"
import { areaConflicts, geometryFromGeoJson } from "./areaConflicts"

const SURVEY_TYPES = ["RGB", "мультиспектральная", "ИК", "LiDAR", "геофизическая"] as const
type Criterion = "Время" | "Налет" | "Компромисс"

type Values = {
  name: string
  survey_type: string
  gsd_cm: string
  work_date: string
  window_start: string
  window_end: string
  timezone: string
  wind_speed_ms: string
  cloud_cover_pct: string
  criterion_mode: Criterion
  criterion_alpha: number
  fleet_id: string
}

function initialValues(task: TaskDetail | null, fleetId: string): Values {
  return {
    name: task?.name ?? "",
    survey_type: task?.survey_type ?? "RGB",
    gsd_cm: task ? String(task.gsd_cm) : "3.0",
    work_date: task?.work_date ?? "",
    window_start: task?.window_start?.slice(0, 5) ?? "",
    window_end: task?.window_end?.slice(0, 5) ?? "",
    timezone: task ? task.timezone ?? "UTC" : browserTimeZone(),
    wind_speed_ms: task?.wind_speed_ms != null ? String(task.wind_speed_ms) : "",
    cloud_cover_pct: task?.cloud_cover_pct != null ? String(task.cloud_cover_pct) : "",
    criterion_mode: (task?.criterion_mode as Criterion) ?? "Время",
    criterion_alpha: task?.criterion_alpha ?? 0.5,
    fleet_id: task?.fleet_id ?? fleetId,
  }
}

function timeZones(current: string): string[] {
  const all = (Intl as unknown as { supportedValuesOf?: (key: string) => string[] }).supportedValuesOf?.("timeZone") ?? []
  const list = all.length ? all : ["UTC", "Europe/Moscow", "Asia/Yekaterinburg", "Asia/Novosibirsk"]
  return list.includes(current) ? list : [current, ...list]
}

/** Координата вершины: правка применяется, только когда в поле число. */
// Шесть знаков — около 10 см; убирает и «хвосты» плавающей точки (55.77899999999).
const show = (value: number) => String(Number(value.toFixed(6)))

function CoordInput({ value, label, onCommit }: { value: number; label: string; onCommit: (v: number) => void }) {
  const [text, setText] = useState(show(value))
  useEffect(() => setText(show(value)), [value])
  const valid = Number.isFinite(Number(text.replace(",", "."))) && text.trim() !== ""
  return (
    <input
      className="input sm mono"
      aria-label={label}
      aria-invalid={valid ? undefined : true}
      value={text}
      onChange={(event) => {
        setText(event.target.value)
        const parsed = Number(event.target.value.replace(",", "."))
        if (event.target.value.trim() !== "" && Number.isFinite(parsed)) onCommit(parsed)
      }}
      onBlur={() => setText(show(value))}
    />
  )
}

export function TaskFormScreen() {
  const { environmentId, taskId } = useParams()
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const toast = useToast()
  const queryClient = useQueryClient()
  const [hidden, toggle] = useToggleSet()

  const existing = useQuery({ queryKey: ["task", taskId], queryFn: () => api.getTask(taskId!), enabled: Boolean(taskId) })
  const task = existing.data ?? null
  const envId = environmentId ?? task?.environment_id ?? ""
  const environment = useQuery({ queryKey: ["environment", envId], queryFn: () => api.getEnvironment(envId), enabled: Boolean(envId) })
  const fleets = useQuery({ queryKey: ["fleets"], queryFn: api.listFleets })

  const [values, setValues] = useState<Values | null>(null)
  const [areaMode, setAreaMode] = useState<"file" | "map" | null>(null)
  const [points, setPoints] = useState<DrawPoint[] | null>(null)
  const [fileArea, setFileArea] = useState<{ file: File; geometry: unknown | null } | null>(null)
  const [issues, setIssues] = useState<Record<string, string>>({})
  const [formError, setFormError] = useState<string | null>(null)
  // Версия, на которой основаны правки формы. После 409 задача
  // перезапрашивается, и без этого повторное «Сохранить» ушло бы с новой
  // версией и молча перезаписало чужую правку устаревшими значениями.
  const [baseVersion, setBaseVersion] = useState<number | null>(null)

  // Начальные значения зависят от загруженной задачи: пока ее нет, форма
  // берет их из initialValues на каждом рендере, а первое изменение
  // фиксирует снимок в состоянии.
  const current = values ?? initialValues(task, params.get("fleet") ?? "")
  const set = <K extends keyof Values>(key: K, value: Values[K]) => setValues({ ...current, [key]: value })

  const prefill = useMemo(() => (task ? polygonToPoints(task.area) : []), [task])
  const mode = areaMode ?? (prefill.length || !task ? "map" : "file")
  const drawPoints = points ?? prefill
  const areaGeometry = mode === "map" ? (drawPoints.length >= 3 ? pointsToPolygon(drawPoints) : null) : fileArea?.geometry ?? task?.area ?? null
  // Файл без полигона видно сразу при выборе, не только после «Создать».
  const areaError = mode === "file" && fileArea && !fileArea.geometry ? "В файле нет полигона области облета" : issues["area"]
  const conflicts = useMemo(() => areaConflicts(environment.data, areaGeometry), [environment.data, areaGeometry])
  const conflictZones = useMemo(() => conflicts.map((c) => c.geometry), [conflicts])

  const save = useMutation({
    mutationFn: async () => {
      const payload: Record<string, unknown> = {
        name: current.name,
        environment_id: envId,
        fleet_id: current.fleet_id,
        survey_type: current.survey_type,
        gsd_cm: current.gsd_cm.replace(",", "."),
        work_date: current.work_date,
        window_start: current.window_start,
        window_end: current.window_end,
        timezone: current.timezone,
        wind_speed_ms: current.wind_speed_ms.replace(",", "."),
        cloud_cover_pct: current.cloud_cover_pct,
        criterion_mode: current.criterion_mode,
      }
      if (current.criterion_mode === "Компромисс") payload["criterion_alpha"] = current.criterion_alpha
      if (mode === "map") payload["area_file"] = geojsonFile(pointsToPolygon(drawPoints))
      else if (fileArea) payload["area_file"] = fileArea.file
      else if (task) payload["area_file"] = geojsonFile(task.area)
      if (task) {
        payload["expected_version"] = baseVersion ?? task.version
        return api.updateTask(task.id, payload)
      }
      return api.createTask(payload)
    },
    onSuccess: (summary) => {
      queryClient.invalidateQueries({ queryKey: ["tasks"] })
      queryClient.invalidateQueries({ queryKey: ["task", summary.id] })
      toast(`${task ? "Задача обновлена" : "Задача создана"}: ${summary.name}`)
      if (summary.daylight_warning) toast(summary.daylight_warning, "warn")
      navigate(`/tasks/${summary.id}`)
    },
    onError: (error: Error) => {
      const byField = Object.fromEntries(fieldIssues(error).map((issue) => [issue.field, issue.message]))
      setIssues(byField)
      if (error instanceof ApiError && error.status === 409) {
        setFormError(error.message)
        if (task) setBaseVersion(baseVersion ?? task.version)
        queryClient.invalidateQueries({ queryKey: ["task", taskId] })
      } else setFormError(Object.keys(byField).length ? "Проверьте отмеченные поля." : error.message)
    },
  })

  const reloadCurrent = () => {
    setValues(null)
    setPoints(null)
    setFileArea(null)
    setAreaMode(null)
    setIssues({})
    setFormError(null)
    setBaseVersion(null)
  }

  const submit = () => {
    setIssues({})
    setFormError(null)
    const local: Record<string, string> = {}
    if (!current.name.trim()) local["name"] = "Укажите наименование"
    if (mode === "map" && drawPoints.length < 3) local["area"] = "Нужно минимум 3 точки, чтобы образовать полигон"
    if (mode === "file" && !fileArea && !task) local["area"] = "Выберите файл области облета"
    if (fileArea && !fileArea.geometry) local["area"] = "В файле нет полигона области облета"
    if (!current.fleet_id) local["fleet_id"] = "Выберите парк"
    if (!current.work_date) local["work_date"] = "Укажите дату работ"
    if (current.window_start && current.window_end && current.window_start >= current.window_end) local["window"] = "Начало окна позже окончания"
    // Диапазоны бэкенд не проверяет; в vanilla их держали min/max у полей.
    const gsd = Number(current.gsd_cm.replace(",", "."))
    if (!(gsd > 0)) local["gsd_cm"] = "GSD — положительное число"
    const wind = current.wind_speed_ms.trim() ? Number(current.wind_speed_ms.replace(",", ".")) : 0
    if (!(wind >= 0)) local["wind_speed_ms"] = "Скорость ветра — число не меньше 0"
    const cloud = current.cloud_cover_pct.trim() ? Number(current.cloud_cover_pct) : 0
    if (!(Number.isInteger(cloud) && cloud >= 0 && cloud <= 100)) local["cloud_cover_pct"] = "Облачность — целое от 0 до 100"
    if (Object.keys(local).length) {
      setIssues(local)
      setFormError("Проверьте отмеченные поля.")
      return
    }
    save.mutate()
  }

  const backTo = task ? `/tasks/${task.id}` : `/catalog/new?env=${envId}${current.fleet_id ? `&fleet=${current.fleet_id}` : ""}`
  useHeaderContext({ title: task ? task.name : "Новая задача", exitTo: task ? "/catalog/history" : "/catalog/new", exitLabel: "Каталог" })
  useStep(STEP.task, [STEP.environment], (index) => {
    if (index === STEP.environment) navigate(task ? `/tasks/${task.id}/environment` : `/environments/${envId}`)
  })

  const fleet = fleets.data?.find((f) => f.id === current.fleet_id)
  const zones = useMemo(() => timeZones(current.timezone), [current.timezone])
  const tz = zoneLabel(current.timezone)

  return (
    <>
      <EnvironmentLayers environment={environment.data ?? null} hidden={hidden} dimmed />
      <DrawAreaLayer active={mode === "map"} points={drawPoints} onAdd={(point) => setPoints([...drawPoints, point])} />
      <AreaLayer area={mode === "file" ? areaGeometry : null} boundsKey={mode === "file" && areaGeometry ? `form-${fileArea?.file.name ?? task?.id}` : null} />
      <ConflictLayer zones={conflictZones} />
      {environment.data ? (
        <LegendSlot>
          <LegendBox>
            <EnvironmentLegend counts={layerCounts(environment.data)} hidden={hidden} onToggle={toggle} />
          </LegendBox>
        </LegendSlot>
      ) : null}
      <Sidebar
        footer={
          <>
            <Link className="btn ghost" to={backTo}>
              Отмена
            </Link>
            <Button variant="primary" className="push" busy={save.isPending} onClick={submit}>
              {task ? "Сохранить" : "Создать задачу"}
            </Button>
          </>
        }
      >
        <Link className="back" to={backTo}>
          <Icon name="back" size={14} />
          {task ? "К задаче" : "Выбор обстановки и парка"}
        </Link>
        {taskId && existing.isPending ? (
          <Skeleton />
        ) : (
          <form
            className="section"
            style={{ gap: 16 }}
            onSubmit={(event) => {
              event.preventDefault()
              submit()
            }}
          >
            <SideHead
              title={task ? "Редактирование задачи" : "Новая задача"}
              sub={`${environment.data?.name ?? "…"} · ${task?.fleet_name ?? fleet?.name ?? "парк не выбран"}`}
            />
            {formError ? (
              <Banner kind="bad" role="alert">
                {formError}
                {task && baseVersion != null && task.version !== baseVersion ? (
                  <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 8, flexWrap: "wrap" }}>
                    <Button size="sm" onClick={reloadCurrent}>
                      Загрузить версию {task.version}
                    </Button>
                    <span className="muted sm">ваши правки в форме будут сброшены</span>
                  </div>
                ) : null}
              </Banner>
            ) : null}
            <Field label="Наименование" error={issues["name"]}>
              <input className="input" value={current.name} onChange={(event) => set("name", event.target.value)} />
            </Field>

            <div className="field">
              <span className="lbl">Область облета</span>
              <Segmented<"file" | "map">
                name="area-mode"
                label="Способ задания области"
                value={mode}
                onChange={setAreaMode}
                options={[
                  { value: "file", label: <><Icon name="file" size={14} />Файл</> },
                  { value: "map", label: <><Icon name="pen" size={14} />Нарисовать</> },
                ]}
              />
              {mode === "map" ? (
                <div className="card flat section" style={{ gap: 6, padding: 10 }}>
                  {drawPoints.length ? (
                    <>
                      <div style={{ display: "grid", gridTemplateColumns: "22px 1fr 1fr 24px", gap: 6 }} className="muted">
                        <span />
                        <span style={{ fontSize: 11 }}>Широта</span>
                        <span style={{ fontSize: 11 }}>Долгота</span>
                        <span />
                      </div>
                      {drawPoints.map((point, index) => (
                        <div key={index} style={{ display: "grid", gridTemplateColumns: "22px 1fr 1fr 24px", gap: 6, alignItems: "center" }}>
                          <span className="mono muted" style={{ fontSize: 12, textAlign: "center" }}>
                            {index + 1}
                          </span>
                          <CoordInput value={point.lat} label={`Широта вершины ${index + 1}`} onCommit={(lat) => setPoints(drawPoints.map((p, i) => (i === index ? { ...p, lat } : p)))} />
                          <CoordInput value={point.lng} label={`Долгота вершины ${index + 1}`} onCommit={(lng) => setPoints(drawPoints.map((p, i) => (i === index ? { ...p, lng } : p)))} />
                          <button type="button" className="btn ghost sm" style={{ padding: 0, width: 24 }} aria-label={`Удалить вершину ${index + 1}`} onClick={() => setPoints(drawPoints.filter((_, i) => i !== index))}>
                            <Icon name="close" size={13} />
                          </button>
                        </div>
                      ))}
                    </>
                  ) : null}
                  <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                    <Button size="sm" disabled={!drawPoints.length} onClick={() => setPoints(drawPoints.slice(0, -1))}>
                      Удалить последнюю
                    </Button>
                    <Button size="sm" variant="ghost" disabled={!drawPoints.length} onClick={() => setPoints([])}>
                      Очистить
                    </Button>
                    <span className="muted" style={{ fontSize: 11.5, marginLeft: "auto" }}>
                      клик по карте — вершина
                    </span>
                  </div>
                </div>
              ) : (
                <FilePicker
                  label={null}
                  accept=".geojson,.json,application/geo+json,application/json"
                  file={fileArea?.file ?? null}
                  onPick={async (file) => setFileArea(file ? { file, geometry: geometryFromGeoJson(await file.text()) } : null)}
                  hint={task && !fileArea ? "Не выбирайте файл, если область не меняется." : "GeoJSON: Polygon, Feature или коллекция из одного объекта."}
                />
              )}
              {areaError ? (
                <span className="err" role="alert">
                  {areaError}
                </span>
              ) : null}
              {conflicts.length ? (
                <Banner kind="warn">
                  Область пересекает {conflicts.length === 1 ? "БПЗ" : "БПЗ:"} {conflicts.map((c) => `«${c.name}»`).join(", ")} — пересечение отмечено на карте. Сохранить можно, участок в зоне сниматься не будет.
                </Banner>
              ) : null}
            </div>

            {task ? null : (
              <Field label="Парк" error={issues["fleet_id"]} hint="Парк задачи после создания не меняется.">
                <select className="select" value={current.fleet_id} onChange={(event) => set("fleet_id", event.target.value)}>
                  <option value="" disabled>
                    {fleets.data?.length ? "Выберите парк" : "Парки не загружены"}
                  </option>
                  {(fleets.data ?? []).map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.name} · {item.ready_count} из {item.total} готовы
                    </option>
                  ))}
                </select>
              </Field>
            )}

            <div className="row2">
              <Field label="Тип съемки" error={issues["survey_type"]}>
                <select className="select" value={current.survey_type} onChange={(event) => set("survey_type", event.target.value)}>
                  {SURVEY_TYPES.map((type) => (
                    <option key={type} value={type}>
                      {type}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="GSD, см/пикс" error={issues["gsd_cm"]}>
                <input className="input mono" inputMode="decimal" value={current.gsd_cm} onChange={(event) => set("gsd_cm", event.target.value)} />
              </Field>
            </div>
            <div className="row2">
              <Field label="Дата работ" error={issues["work_date"]}>
                <input className="input mono" type="date" value={current.work_date} onChange={(event) => set("work_date", event.target.value)} />
              </Field>
              <Field label="Часовой пояс" error={issues["timezone"]}>
                <select className="select" value={current.timezone} onChange={(event) => set("timezone", event.target.value)}>
                  {zones.map((zone) => (
                    <option key={zone} value={zone}>
                      {zone}
                    </option>
                  ))}
                </select>
              </Field>
            </div>
            <div className="field">
              <div className="row2">
                <Field label={`Окно с (${tz})`}>
                  <input className="input mono" type="time" aria-invalid={issues["window"] ? true : undefined} value={current.window_start} onChange={(event) => set("window_start", event.target.value)} />
                </Field>
                <Field label={`по (${tz})`}>
                  <input className="input mono" type="time" aria-invalid={issues["window"] ? true : undefined} value={current.window_end} onChange={(event) => set("window_end", event.target.value)} />
                </Field>
              </div>
              {issues["window"] ? (
                <span className="err" role="alert">
                  {issues["window"]}
                </span>
              ) : (
                <span className="hint">Необязательно. Без окна — весь световой день.</span>
              )}
            </div>
            <div className="row2">
              <Field label="Ветер, м/с" error={issues["wind_speed_ms"]}>
                <input className="input mono" inputMode="decimal" value={current.wind_speed_ms} onChange={(event) => set("wind_speed_ms", event.target.value)} />
              </Field>
              <Field label="Облачность, %" error={issues["cloud_cover_pct"]}>
                <input className="input mono" inputMode="numeric" value={current.cloud_cover_pct} onChange={(event) => set("cloud_cover_pct", event.target.value)} />
              </Field>
            </div>
            <div className="field">
              <span className="lbl">Критерий оптимизации</span>
              <Segmented<Criterion>
                name="criterion"
                label="Критерий оптимизации"
                value={current.criterion_mode}
                onChange={(value) => set("criterion_mode", value)}
                options={[
                  { value: "Время", label: "Время" },
                  { value: "Налет", label: "Налет" },
                  { value: "Компромисс", label: "Компромисс" },
                ]}
              />
              {issues["criterion_mode"] ? <span className="err">{issues["criterion_mode"]}</span> : null}
            </div>
            {current.criterion_mode === "Компромисс" ? (
              <div className="field">
                <div style={{ display: "flex", justifyContent: "space-between" }}>
                  <label htmlFor="alpha">Вес времени α</label>
                  <span className="mono sm">{current.criterion_alpha.toFixed(2).replace(".", ",")}</span>
                </div>
                <input id="alpha" className="range" type="range" min="0" max="1" step="0.05" value={current.criterion_alpha} onChange={(event) => set("criterion_alpha", Number(event.target.value))} />
                <div className="muted sm" style={{ display: "flex", justifyContent: "space-between" }}>
                  <span>налет</span>
                  <span>время</span>
                </div>
                {issues["criterion_alpha"] ? <span className="err">{issues["criterion_alpha"]}</span> : null}
              </div>
            ) : null}
            <button type="submit" hidden />
          </form>
        )}
      </Sidebar>
    </>
  )
}
