/**
 * Экран «Парк БВС» (ПБС).
 *
 * Парков теперь несколько, каждый — именованная сущность со своей локацией,
 * поэтому экран двухуровневый: список парков -> экземпляры выбранного парка ->
 * карточка экземпляра. Прежде парк был один, и список экземпляров открывался
 * сразу.
 */
import { useMemo, useState } from "react"
import { useNavigate } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api, type FleetInstance, type ModelSpec } from "@/api/client"
import { Sidebar, useStep } from "@/app/AppShell"
import { Modal } from "@/shared/ui/Modal"
import { useToast } from "@/shared/ui/Toasts"

const READINESS_STATUSES = ["Готов", "Недоступен", "На обслуживании"] as const

function statusTone(status: string): string {
  if (status === "Готов") return "ok"
  if (status === "Недоступен") return "bad"
  return "draft"
}

function ModelCard({ model }: { model: ModelSpec }) {
  return (
    <dl className="counts" style={{ marginBottom: 8 }}>
      <dt style={{ gridColumn: "1/-1", fontWeight: 700, color: "var(--ink)" }}>
        {model.name} ({model.uav_type})
      </dt>
      <dt>Скорость</dt>
      <dd>
        {model.speed_min_ms}–{model.speed_max_ms} м/с
      </dd>
      <dt>Время полета</dt>
      <dd>до {model.max_flight_time_min} мин</dd>
      <dt>Длина маршрута</dt>
      <dd>{model.max_route_km != null ? `до ${model.max_route_km} км` : "—"}</dd>
      <dt>Макс. ветер</dt>
      <dd>{model.max_wind_ms} м/с</dd>
      <dt>Высоты</dt>
      <dd>
        {model.height_min_m}
        {model.height_max_m != null ? `–${model.height_max_m}` : "+"} м
      </dd>
      <dt>Дальность связи</dt>
      <dd>{model.comm_range_km} км</dd>
      <dt>Нагрузка</dt>
      <dd>{model.compatible_cameras.join(", ")}</dd>
    </dl>
  )
}

function InstanceCard({
  instance,
  model,
  onBack,
}: {
  instance: FleetInstance
  model?: ModelSpec
  onBack: () => void
}) {
  return (
    <>
      <a
        href="#"
        className="back-link"
        onClick={(event) => {
          event.preventDefault()
          onBack()
        }}
      >
        ← К списку парка
      </a>
      <p className="sb-title">{instance.inventory_number}</p>
      <span className={`status-badge ${statusTone(instance.status)}`}>{instance.status}</span>
      {instance.error ? <div className="warning-box">{instance.error}</div> : null}
      <dl className="counts">
        <dt>Модель</dt>
        <dd>{instance.model_name}</dd>
        <dt>Базовый ВПП</dt>
        <dd>{instance.base_launch_site || "—"}</dd>
        {model ? (
          <>
            <dt>Тип</dt>
            <dd>{model.uav_type}</dd>
            <dt>Скорость</dt>
            <dd>
              {model.speed_min_ms}–{model.speed_max_ms} м/с
            </dd>
            <dt>Время полета</dt>
            <dd>до {model.max_flight_time_min} мин</dd>
            <dt>Длина маршрута</dt>
            <dd>{model.max_route_km != null ? `до ${model.max_route_km} км` : "—"}</dd>
            <dt>Макс. ветер</dt>
            <dd>{model.max_wind_ms} м/с</dd>
            <dt>Высоты</dt>
            <dd>
              {model.height_min_m}
              {model.height_max_m != null ? `–${model.height_max_m}` : "+"} м
            </dd>
            <dt>Дальность связи</dt>
            <dd>{model.comm_range_km} км</dd>
            <dt>Нагрузка</dt>
            <dd>{model.compatible_cameras.join(", ")}</dd>
          </>
        ) : null}
      </dl>
    </>
  )
}

function UploadFleetModal({ onClose }: { onClose: () => void }) {
  const [error, setError] = useState<string | null>(null)
  const queryClient = useQueryClient()
  const toast = useToast()

  const upload = useMutation({
    mutationFn: (values: { name: string; locationName: string; file: File }) =>
      api.uploadFleet(values.name, values.file, values.locationName || undefined),
    onSuccess: (summary) => {
      queryClient.invalidateQueries({ queryKey: ["fleets"] })
      toast(`Парк «${summary.name}» загружен: ${summary.ready_count} из ${summary.total} готовы`)
      onClose()
    },
    onError: (err: Error) => setError(err.message || "Ошибка загрузки"),
  })

  return (
    <Modal title="Загрузка парка БВС" onClose={onClose}>
      <form
        onSubmit={(event) => {
          event.preventDefault()
          setError(null)
          const form = new FormData(event.currentTarget)
          const name = String(form.get("name") ?? "").trim()
          const locationName = String(form.get("location_name") ?? "").trim()
          const file = form.get("file")
          if (!name) {
            setError("Укажите название парка")
            return
          }
          if (!(file instanceof File) || !file.size) {
            setError("Выберите файл парка")
            return
          }
          upload.mutate({ name, locationName, file })
        }}
      >
        <label htmlFor="fleet-name">Название парка</label>
        <input type="text" id="fleet-name" name="name" placeholder="Парк Уктус" required />

        <label htmlFor="fleet-location">Расположение (необязательно)</label>
        <input type="text" id="fleet-location" name="location_name" placeholder="Екатеринбург" />
        <p className="hint">
          Координаты парка определяются по файлу — усредняются локации экземпляров. Это
          название нужно только для подписи в списке.
        </p>

        <label htmlFor="fleet-file">Файл JSON или CSV</label>
        <input
          type="file"
          id="fleet-file"
          name="file"
          accept=".json,.csv,application/json,text/csv"
          required
        />
        <p className="hint">
          Перечень экземпляров БВС: инвентарный номер, модель, статус готовности, базовая ВПП,
          координаты стоянки (location_lat, location_lon). Без координат хотя бы у одного
          экземпляра парк не принимается: расчёт строит точку взлёта по ним.
        </p>
        {error ? <p className="error">{error}</p> : null}
        <div className="actions">
          <button type="button" className="btn" onClick={onClose}>
            Отмена
          </button>
          <button type="submit" className="btn primary" disabled={upload.isPending}>
            Проверить и сохранить
          </button>
        </div>
      </form>
    </Modal>
  )
}

export function FleetScreen() {
  const navigate = useNavigate()
  const [uploading, setUploading] = useState(false)
  const [selected, setSelected] = useState<string | null>(null)
  const [search, setSearch] = useState("")
  const [modelFilter, setModelFilter] = useState("")
  const [statusFilter, setStatusFilter] = useState("")

  const [selectedFleet, setSelectedFleet] = useState<string | null>(null)
  const fleets = useQuery({ queryKey: ["fleets"], queryFn: api.listFleets })
  const fleet = useQuery({
    queryKey: ["fleet", selectedFleet],
    queryFn: () => api.getFleet(selectedFleet as string),
    enabled: selectedFleet != null,
  })
  const models = useQuery({ queryKey: ["models"], queryFn: api.listModels })

  const instances = fleet.data?.instances ?? []
  const filtered = useMemo(
    () =>
      instances.filter((instance) => {
        if (search && !instance.inventory_number.toLowerCase().includes(search.toLowerCase())) {
          return false
        }
        if (modelFilter && instance.model_key !== modelFilter) return false
        if (statusFilter && instance.status !== statusFilter) return false
        return true
      }),
    [instances, search, modelFilter, statusFilter],
  )

  const current = selected
    ? instances.find((instance) => instance.inventory_number === selected)
    : undefined

  const fleetList = (
    <>
      <p className="sb-sub">
        {fleets.data?.length
          ? "Выберите парк, чтобы посмотреть его экземпляры."
          : "Парков нет. Загрузите файл JSON или CSV с перечнем экземпляров БВС."}
      </p>
      <ul className="task-list">
        {(fleets.data ?? []).map((item) => (
          <li key={item.id}>
            <a
              href="#"
              onClick={(event) => {
                event.preventDefault()
                setSelected(null)
                setSelectedFleet(item.id)
              }}
            >
              <span className="t-name">{item.name}</span>
              <span className="t-meta">
                {item.location_name
                  ? item.location_name
                  : `${item.location_lat.toFixed(3)}, ${item.location_lon.toFixed(3)}`}{" "}
                ·{" "}
                <span
                  className={`status-badge ${item.status === "Корректна" ? "ok" : "bad"}`}
                  style={{ margin: 0, padding: "1px 7px" }}
                >
                  {item.ready_count}/{item.total} готовы
                </span>
              </span>
            </a>
          </li>
        ))}
      </ul>
    </>
  )

  const sidebar = current ? (
    <InstanceCard
      instance={current}
      model={models.data?.find((m) => m.key === current.model_key)}
      onBack={() => setSelected(null)}
    />
  ) : (
    <>
      <a
        href="#"
        className="back-link"
        onClick={(event) => {
          event.preventDefault()
          navigate(-1)
        }}
      >
        ← Назад
      </a>
      <p className="sb-title">Парк БВС</p>
      <button
        className="btn primary"
        style={{ width: "100%", marginBottom: 14 }}
        onClick={() => setUploading(true)}
      >
        Загрузить парк
      </button>

      {!fleet.data ? (
        fleetList
      ) : (
        <>
          <a
            href="#"
            className="back-link"
            onClick={(event) => {
              event.preventDefault()
              setSelected(null)
              setSelectedFleet(null)
            }}
          >
            ← Ко всем паркам
          </a>
          <p className="sb-title" style={{ marginTop: 0 }}>
            {fleet.data.name}
          </p>
          <p className="sb-sub">
            {fleet.data.location_name ? `${fleet.data.location_name} · ` : ""}
            загружен {new Date(fleet.data.uploaded_at).toLocaleString("ru-RU")}
          </p>
          <span className={fleet.data.status === "Корректна" ? "status-badge ok" : "status-badge bad"}>
            {fleet.data.status} · {fleet.data.ready_count}/{fleet.data.total} готовы
          </span>

          {fleet.data.status !== "Корректна" ? (
            <ul className="errors-list">
              {fleet.data.errors.map((issue, index) => (
                <li key={index}>
                  <span className="layer-tag">{issue.inventory_number || "—"}</span>
                  {issue.message}
                </li>
              ))}
            </ul>
          ) : null}

          <div className="row2" style={{ marginBottom: 8 }}>
            <input
              type="text"
              placeholder="Поиск по номеру"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
            <select value={modelFilter} onChange={(event) => setModelFilter(event.target.value)}>
              <option value="">Все модели</option>
              {(models.data ?? []).map((model) => (
                <option key={model.key} value={model.key}>
                  {model.name}
                </option>
              ))}
            </select>
          </div>
          <div className="row2" style={{ marginBottom: 8 }}>
            <select
              style={{ gridColumn: "1/-1" }}
              value={statusFilter}
              onChange={(event) => setStatusFilter(event.target.value)}
            >
              <option value="">Все статусы</option>
              {READINESS_STATUSES.map((status) => (
                <option key={status} value={status}>
                  {status}
                </option>
              ))}
            </select>
          </div>

          {filtered.length ? (
            <ul className="task-list">
              {filtered.map((instance) => (
                <li key={instance.inventory_number}>
                  <a
                    href="#"
                    onClick={(event) => {
                      event.preventDefault()
                      setSelected(instance.inventory_number)
                    }}
                  >
                    <span className="t-name">
                      {instance.inventory_number}
                      {instance.valid === false ? " ⚠" : ""}
                    </span>
                    <span className="t-meta">
                      {instance.model_name} ·{" "}
                      <span
                        className={`status-badge ${statusTone(instance.status)}`}
                        style={{ margin: 0, padding: "1px 7px" }}
                      >
                        {instance.status}
                      </span>
                    </span>
                  </a>
                </li>
              ))}
            </ul>
          ) : (
            <p className="sb-sub">Ничего не найдено.</p>
          )}
        </>
      )}

      <div className="history">
        <h3>Справочник моделей</h3>
        {(models.data ?? []).map((model) => (
          <ModelCard key={model.key} model={model} />
        ))}
      </div>
    </>
  )

  useStep(0, [])

  return (
    <>
      <Sidebar>{sidebar}</Sidebar>
      {uploading ? <UploadFleetModal onClose={() => setUploading(false)} /> : null}
    </>
  )
}
