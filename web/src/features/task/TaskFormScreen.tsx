/**
 * Форма задачи (ЗАД.ФТ.9-10, ЗАД.ФТ.12).
 *
 * Два правила перенесены из legacy буквально, и оба неочевидны:
 *
 * * пустое поле означает «ключ не отправляем вовсе» — `Form(None)` в FastAPI
 *   разбирает пустую строку как значение и отвечает 422;
 * * область всегда уходит файлом, даже при правке без её изменения, — бэкенд
 *   требует `area_file` и на PUT, поэтому форма держит текущую область и
 *   воспроизводит её файлом.
 */
import { useMemo, useState } from "react"
import { useNavigate, useParams } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { ApiError, api, geojsonFile } from "@/api/client"
import { Sidebar, useStep } from "@/app/AppShell"
import { EnvironmentLayers } from "@/map/EnvironmentLayers"
import { AreaLayer } from "@/map/AreaLayer"
import { DrawAreaLayer, pointsToPolygon, polygonToPoints, type DrawPoint } from "@/map/DrawAreaLayer"
import { useToast } from "@/shared/ui/Toasts"

const SURVEY_TYPES = ["RGB", "мультиспектральная", "ИК", "LiDAR", "геофизическая"] as const
const CRITERIA = ["Время", "Налет", "Компромисс"] as const
const NO_HIDDEN: ReadonlySet<string> = new Set()

export function TaskFormScreen() {
  const { environmentId, taskId } = useParams()
  const navigate = useNavigate()
  const toast = useToast()
  const queryClient = useQueryClient()

  const existing = useQuery({
    queryKey: ["task", taskId],
    queryFn: () => api.getTask(taskId!),
    enabled: Boolean(taskId),
  })
  const task = existing.data ?? null
  const envId = environmentId ?? task?.environment_id ?? ""

  const environment = useQuery({
    queryKey: ["environment", envId],
    queryFn: () => api.getEnvironment(envId),
    enabled: Boolean(envId),
  })

  // Парк — часть постановки задачи и после создания не меняется.
  const fleets = useQuery({ queryKey: ["fleets"], queryFn: api.listFleets })

  const prefill = useMemo(() => (task ? polygonToPoints(task.area) : []), [task])
  const [areaMode, setAreaMode] = useState<"file" | "map" | null>(null)
  const [points, setPoints] = useState<DrawPoint[] | null>(null)
  const [criterion, setCriterion] = useState<string | null>(null)
  const [alpha, setAlpha] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)

  // Начальные значения зависят от загруженной задачи, поэтому состояние
  // инициализируется лениво, а не в useState — иначе первый рендер с
  // `task === null` зафиксировал бы пустую форму.
  const mode = areaMode ?? (prefill.length ? "map" : "file")
  const drawPoints = points ?? prefill
  const currentCriterion = criterion ?? task?.criterion_mode ?? "Время"
  const currentAlpha = alpha ?? task?.criterion_alpha ?? 0.5

  const save = useMutation({
    mutationFn: async (form: HTMLFormElement) => {
      const data = new FormData(form)
      const values: Record<string, unknown> = {
        name: data.get("name"),
        environment_id: envId,
        fleet_id: data.get("fleet_id"),
        survey_type: data.get("survey_type"),
        gsd_cm: data.get("gsd_cm"),
        work_date: data.get("work_date"),
        window_start: data.get("window_start"),
        window_end: data.get("window_end"),
        wind_speed_ms: data.get("wind_speed_ms"),
        cloud_cover_pct: data.get("cloud_cover_pct"),
        criterion_mode: currentCriterion,
      }
      if (currentCriterion === "Компромисс") values["criterion_alpha"] = currentAlpha

      if (mode === "map") {
        values["area_file"] = geojsonFile(pointsToPolygon(drawPoints))
      } else {
        const picked = data.get("area_file")
        if (picked instanceof File && picked.size) values["area_file"] = picked
        else if (task) values["area_file"] = geojsonFile(task.area)
      }

      if (task) {
        values["expected_version"] = task.version
        return api.updateTask(task.id, values)
      }
      return api.createTask(values)
    },
    onSuccess: (summary) => {
      queryClient.invalidateQueries({ queryKey: ["tasks"] })
      queryClient.invalidateQueries({ queryKey: ["task", summary.id] })
      toast(
        `${task ? "Задача обновлена: " : "Задача создана: "}${summary.name}`,
        Boolean(summary.daylight_warning),
      )
      navigate(`/tasks/${summary.id}`)
    },
    onError: (err: Error) => {
      // ЗАД.ФТ.12: конфликт версий показываем как есть — сообщение бэкенда
      // уже содержит имя того, кто изменил задачу первым.
      setError(err instanceof ApiError && err.status === 409 ? err.message : err.message)
    },
  })

  const sidebar = (
    <>
      <a
        href="#"
        className="back-link"
        onClick={(event) => {
          event.preventDefault()
          navigate(task ? `/tasks/${task.id}` : `/environments/${envId}/tasks`)
        }}
      >
        ← {task ? "К задаче" : "К списку задач"}
      </a>
      <p className="sb-title">{task ? "Редактирование задачи" : "Новая задача"}</p>

      <form
        className="sb-form"
        onSubmit={(event) => {
          event.preventDefault()
          setError(null)
          if (mode === "map" && drawPoints.length < 3) {
            setError("область облета: нужно минимум 3 точки, чтобы образовать полигон")
            return
          }
          save.mutate(event.currentTarget)
        }}
      >
        <label>Наименование</label>
        <input type="text" name="name" required defaultValue={task?.name ?? ""} />

        <label>Область облета</label>
        <div className="mode-toggle">
          <label>
            <input
              type="radio"
              name="area_mode"
              value="file"
              checked={mode === "file"}
              onChange={() => setAreaMode("file")}
            />
            <span>Файл</span>
          </label>
          <label>
            <input
              type="radio"
              name="area_mode"
              value="map"
              checked={mode === "map"}
              onChange={() => setAreaMode("map")}
            />
            <span>Нарисовать на карте</span>
          </label>
        </div>

        <div hidden={mode === "map"}>
          <input
            type="file"
            name="area_file"
            accept=".geojson,.json,application/geo+json,application/json"
            required={mode === "file" && !task}
          />
          {task ? (
            <p className="hint">Не выбирайте файл, если область облета не меняется.</p>
          ) : null}
        </div>

        <div className="area-draw-block" hidden={mode === "file"}>
          <p className="hint">
            Кликайте на карте — каждый клик добавляет точку контура (минимум 3). Координаты точки
            можно скорректировать в текстовых полях ниже.
          </p>
          <div>
            {drawPoints.map((point, index) => (
              <div className="draw-point-row" key={index}>
                <span className="dp-num">{index + 1}</span>
                <input
                  type="text"
                  value={point.lat}
                  onChange={(event) =>
                    setPoints(
                      drawPoints.map((p, i) =>
                        i === index ? { ...p, lat: Number(event.target.value) || 0 } : p,
                      ),
                    )
                  }
                />
                <input
                  type="text"
                  value={point.lng}
                  onChange={(event) =>
                    setPoints(
                      drawPoints.map((p, i) =>
                        i === index ? { ...p, lng: Number(event.target.value) || 0 } : p,
                      ),
                    )
                  }
                />
                <button
                  type="button"
                  className="dp-remove"
                  onClick={() => setPoints(drawPoints.filter((_, i) => i !== index))}
                >
                  ×
                </button>
              </div>
            ))}
          </div>
          <div className="draw-actions">
            <button type="button" className="btn" onClick={() => setPoints(drawPoints.slice(0, -1))}>
              Удалить последнюю
            </button>
            <button type="button" className="btn" onClick={() => setPoints([])}>
              Очистить
            </button>
          </div>
        </div>

        <label>Парк БВС</label>
        {task ? (
          <>
            <input type="hidden" name="fleet_id" value={task.fleet_id} />
            <input type="text" value={task.fleet_name} disabled />
            <p className="hint">
              Парк задачи не меняется: по нему уже считались планы. Нужен другой — создайте
              новую задачу.
            </p>
          </>
        ) : (
          <>
            <select name="fleet_id" required defaultValue="">
              <option value="" disabled>
                {fleets.data?.length ? "Выберите парк" : "Парки не загружены"}
              </option>
              {(fleets.data ?? []).map((fleet) => (
                <option key={fleet.id} value={fleet.id}>
                  {fleet.name}
                  {fleet.location_name ? ` — ${fleet.location_name}` : ""} ({fleet.ready_count}{" "}
                  готовых)
                </option>
              ))}
            </select>
            <p className="hint">
              Парк должен базироваться не дальше 150 км от обстановки — иначе БВС физически не
              долетят до области работ.
            </p>
          </>
        )}

        <label>Тип съемки</label>
        <select name="survey_type" defaultValue={task?.survey_type ?? "RGB"}>
          {SURVEY_TYPES.map((type) => (
            <option key={type} value={type}>
              {type}
            </option>
          ))}
        </select>

        <div className="row2">
          <div>
            <label>Целевое разрешение GSD, см</label>
            <input
              type="number"
              step="0.1"
              min="0.1"
              name="gsd_cm"
              required
              defaultValue={task?.gsd_cm ?? "3.0"}
            />
          </div>
          <div>
            <label>Дата работ</label>
            <input type="date" name="work_date" required defaultValue={task?.work_date ?? ""} />
          </div>
        </div>

        <div className="row2">
          <div>
            <label>Окно работ, с</label>
            <input type="time" name="window_start" defaultValue={task?.window_start?.slice(0, 5) ?? ""} />
          </div>
          <div>
            <label>Окно работ, по</label>
            <input type="time" name="window_end" defaultValue={task?.window_end?.slice(0, 5) ?? ""} />
          </div>
        </div>

        <div className="row2">
          <div>
            <label>Скорость ветра, м/с</label>
            <input
              type="number"
              step="0.1"
              min="0"
              name="wind_speed_ms"
              defaultValue={task?.wind_speed_ms ?? ""}
            />
          </div>
          <div>
            <label>Облачность, %</label>
            <input
              type="number"
              step="1"
              min="0"
              max="100"
              name="cloud_cover_pct"
              defaultValue={task?.cloud_cover_pct ?? ""}
            />
          </div>
        </div>

        <label>Критерий оптимизации</label>
        <div className="criterion-group">
          {CRITERIA.map((value) => (
            <label key={value}>
              <input
                type="radio"
                name="criterion_mode"
                value={value}
                checked={currentCriterion === value}
                onChange={() => setCriterion(value)}
              />
              <span>{value}</span>
            </label>
          ))}
        </div>
        <div className="alpha-row" hidden={currentCriterion !== "Компромисс"}>
          <label>Вес времени α = {currentAlpha}</label>
          <input
            type="range"
            min="0"
            max="1"
            step="0.05"
            value={currentAlpha}
            onChange={(event) => setAlpha(Number(event.target.value))}
          />
        </div>

        {error ? <p className="error field-error">{error}</p> : null}
        <div className="actions">
          <button
            type="button"
            className="btn"
            onClick={() => navigate(task ? `/tasks/${task.id}` : `/environments/${envId}/tasks`)}
          >
            Отмена
          </button>
          <button type="submit" className="btn primary" disabled={save.isPending}>
            {task ? "Сохранить" : "Создать задачу"}
          </button>
        </div>
      </form>
    </>
  )

  useStep(1, [0], (index) => index === 0 && navigate(`/environments/${envId}`))

  return (
    <>
      <EnvironmentLayers
        environment={environment.data ?? null}
        hidden={NO_HIDDEN}
        dimmed={mode === "map"}
      />
      <DrawAreaLayer
        active={mode === "map"}
        points={drawPoints}
        onAdd={(point) => setPoints([...drawPoints, point])}
      />
      <AreaLayer
        area={mode === "file" && task ? task.area : null}
        boundsKey={mode === "file" && task ? task.id : null}
      />
      <Sidebar>{sidebar}</Sidebar>
    </>
  )
}
