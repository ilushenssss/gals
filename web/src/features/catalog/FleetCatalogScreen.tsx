/**
 * Каталог, вкладка «Парк» (ПБС): парки → экземпляры парка → карточка
 * экземпляра. Выбор живет в URL (`/catalog/fleet/:fleetId/:inventory`) —
 * переживает перезагрузку, и на экземпляр можно дать ссылку.
 *
 * Пока ничего не загружено, вкладка ведет оператора по первым шагам, а на
 * карте — кнопка «Загрузить данные» (ИНТ.ФТ.3).
 */
import { useMemo, useState } from "react"
import { Link, useNavigate, useParams } from "react-router-dom"
import { useQuery } from "@tanstack/react-query"
import { api } from "@/api/client"
import type { FleetInstance, ModelSpec } from "@/api/client"
import { OverlaySlot, Sidebar } from "@/app/AppShell"
import { FleetLayer } from "@/map/FleetLayer"
import { Button } from "@/shared/ui/Button"
import { KeyValue, SideHead } from "@/shared/ui/Display"
import { Icon } from "@/shared/ui/Icon"
import { EmptyState, ErrorState, Skeleton } from "@/shared/ui/States"
import { StatusBadge } from "@/shared/ui/StatusBadge"
import { formatDateTime } from "@/shared/format"
import { CatalogTabs } from "./CatalogTabs"
import { UploadEnvironmentModal, UploadFleetModal } from "./UploadModals"

const READINESS = ["Готов", "Недоступен", "На обслуживании"] as const

function modelRows(model: ModelSpec) {
  return [
    ["Тип", model.uav_type],
    ["Скорость", <span className="mono">{model.speed_min_ms}–{model.speed_max_ms} м/с</span>],
    ["Время полета", <span className="mono">до {model.max_flight_time_min} мин</span>],
    ["Длина маршрута", model.max_route_km != null ? <span className="mono">до {model.max_route_km} км</span> : "—"],
    ["Предельный ветер", <span className="mono">{model.max_wind_ms} м/с</span>],
    ["Высоты", <span className="mono">{model.height_min_m}{model.height_max_m != null ? `–${model.height_max_m}` : "+"} м</span>],
    ["Дальность связи", <span className="mono">{model.comm_range_km} км</span>],
    ["Нагрузка", model.compatible_cameras.join(", ")],
  ] as const
}

// Статус невалидного экземпляра — подстановка бэкенда, а не данные файла:
// «Готов» рядом с «0 из N готовы» противоречит счетчику.
function InstanceStatus({ instance }: { instance: FleetInstance }) {
  if (instance.valid === false) return <StatusBadge tone="bad">Ошибка</StatusBadge>
  return <StatusBadge status={instance.status} />
}

function InstanceCard({ instance, model }: { instance: FleetInstance; model?: ModelSpec }) {
  return (
    <div className="card flat section">
      <div className="section-head">
        <span className="mono" style={{ fontWeight: 700 }}>
          {instance.inventory_number}
        </span>
        <InstanceStatus instance={instance} />
      </div>
      {instance.error ? <div className="err">{instance.error}</div> : null}
      <KeyValue rows={[["Модель", instance.model_name], ["Базовая ВПП", instance.base_launch_site || "—"], ...(model ? modelRows(model) : [])]} />
    </div>
  )
}

function Onboarding({ onEnvironment, onFleet }: { onEnvironment: () => void; onFleet: () => void }) {
  const steps = [
    { icon: "map" as const, title: "Загрузите обстановку", meta: "ВПП, площадки, пространство, БПЗ, препятствия · GeoJSON или KML", action: onEnvironment },
    { icon: "plane" as const, title: "Загрузите парк БВС", meta: "Экземпляры с моделью, базовой ВПП и статусом · JSON или CSV", action: onFleet },
  ]
  return (
    <>
      <SideHead title="С чего начать" sub="Три шага до первого плана. Порядок первых двух не важен." />
      <ol className="list">
        {steps.map((step) => (
          <li key={step.title}>
            <button type="button" className="item" onClick={step.action}>
              <span className="dot cur" style={{ width: 28, height: 28 }}>
                <Icon name={step.icon} size={15} />
              </span>
              <span className="body-col">
                <span className="t">{step.title}</span>
                <span className="m">{step.meta}</span>
              </span>
            </button>
          </li>
        ))}
        <li>
          <div className="item" aria-disabled="true">
            <span className="dot" style={{ width: 28, height: 28 }}>
              <Icon name="pen" size={15} />
            </span>
            <span className="body-col">
              <span className="t">Создайте задачу</span>
              <span className="m">Станет доступно после первых двух шагов</span>
            </span>
          </div>
        </li>
      </ol>
    </>
  )
}

export function FleetCatalogScreen() {
  const { fleetId, inventory } = useParams()
  const navigate = useNavigate()
  const [uploadingFleet, setUploadingFleet] = useState(false)
  const [uploadingEnv, setUploadingEnv] = useState(false)
  const [search, setSearch] = useState("")
  const [modelFilter, setModelFilter] = useState("")
  const [statusFilter, setStatusFilter] = useState("")

  const fleets = useQuery({ queryKey: ["fleets"], queryFn: api.listFleets })
  const environments = useQuery({ queryKey: ["environments"], queryFn: api.listEnvironments })
  const fleet = useQuery({ queryKey: ["fleet", fleetId], queryFn: () => api.getFleet(fleetId!), enabled: Boolean(fleetId) })
  const models = useQuery({ queryKey: ["models"], queryFn: api.listModels })

  const instances = useMemo(() => fleet.data?.instances ?? [], [fleet.data])
  const filtered = useMemo(
    () =>
      instances.filter((instance) => {
        if (search && !instance.inventory_number.toLowerCase().includes(search.toLowerCase())) return false
        if (modelFilter && instance.model_key !== modelFilter) return false
        if (statusFilter && instance.status !== statusFilter) return false
        return true
      }),
    [instances, search, modelFilter, statusFilter],
  )
  const current = inventory ? instances.find((i) => i.inventory_number === inventory) : undefined
  const fleetModels = useMemo(() => {
    const keys = new Set(instances.map((i) => i.model_key))
    return (models.data ?? []).filter((m) => keys.has(m.key))
  }, [instances, models.data])

  const openFleet = (id: string) => {
    setSearch("")
    setModelFilter("")
    setStatusFilter("")
    navigate(`/catalog/fleet/${id}`)
  }
  const openInstance = (number: string) =>
    navigate(number === inventory ? `/catalog/fleet/${fleetId}` : `/catalog/fleet/${fleetId}/${encodeURIComponent(number)}`)

  const nothingLoaded = fleets.data?.length === 0 && environments.data?.length === 0

  let body
  if (fleetId) {
    if (fleet.isPending) body = <Skeleton />
    else if (fleet.isError) body = <ErrorState error={fleet.error} onRetry={() => fleet.refetch()} />
    else if (!fleet.data)
      body = (
        <EmptyState icon="error" title="Парк не найден" action={<Link className="btn sm" to="/catalog/fleet">Ко всем паркам</Link>}>
          Возможно, ссылка устарела.
        </EmptyState>
      )
    else {
      const data = fleet.data
      body = (
        <>
          <SideHead
            back={
              <Link className="back" to="/catalog/fleet">
                <Icon name="back" size={14} />
                Все парки
              </Link>
            }
            title={data.name}
            sub={`${data.location_name ? `${data.location_name} · ` : ""}загружен ${formatDateTime(data.uploaded_at)}`}
          />
          <div style={{ display: "flex", gap: 6, marginTop: -8 }}>
            <StatusBadge status={data.status} />
            <StatusBadge tone="info">
              {data.ready_count} из {data.total} готовы
            </StatusBadge>
          </div>
          {data.errors.length ? (
            <ul className="list">
              {data.errors.map((issue, index) => (
                <li key={index} className="viol hl" style={{ margin: 0 }}>
                  <span>
                    <b className="mono">{issue.inventory_number || "—"}</b> — {issue.message}
                  </span>
                </li>
              ))}
            </ul>
          ) : null}
          <div className="section">
            <div className="input-icon">
              <Icon name="search" />
              <input className="input" aria-label="Поиск по инвентарному номеру" placeholder="Инвентарный номер" value={search} onChange={(event) => setSearch(event.target.value)} />
            </div>
            <div className="row2">
              <select className="select" aria-label="Модель" value={modelFilter} onChange={(event) => setModelFilter(event.target.value)}>
                <option value="">Все модели</option>
                {fleetModels.map((model) => (
                  <option key={model.key} value={model.key}>
                    {model.name}
                  </option>
                ))}
              </select>
              <select className="select" aria-label="Статус" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
                <option value="">Все статусы</option>
                {READINESS.map((status) => (
                  <option key={status} value={status}>
                    {status}
                  </option>
                ))}
              </select>
            </div>
          </div>
          {filtered.length ? (
            <ul className="list">
              {filtered.map((instance) => (
                <li key={instance.inventory_number}>
                  <button type="button" className="item" aria-current={instance.inventory_number === inventory} onClick={() => openInstance(instance.inventory_number)}>
                    <span className="body-col">
                      <span className="t mono">
                        {instance.inventory_number}
                        {instance.valid === false ? <span style={{ color: "var(--danger)" }}> ⚠</span> : null}
                      </span>
                      <span className="m">
                        {instance.model_name}
                        {instance.base_launch_site ? ` · ${instance.base_launch_site}` : ""}
                      </span>
                    </span>
                    <InstanceStatus instance={instance} />
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState icon="search" title="Ничего не найдено">
              Измените поиск или фильтры.
            </EmptyState>
          )}
          {current ? <InstanceCard instance={current} model={models.data?.find((m) => m.key === current.model_key)} /> : null}
        </>
      )
    }
  } else if (fleets.isPending) body = <Skeleton />
  else if (fleets.isError) body = <ErrorState error={fleets.error} onRetry={() => fleets.refetch()} />
  else if (!fleets.data.length)
    body = nothingLoaded ? (
      <Onboarding onEnvironment={() => setUploadingEnv(true)} onFleet={() => setUploadingFleet(true)} />
    ) : (
      <EmptyState icon="plane" title="Парков пока нет" dashed action={<Button icon="upload" onClick={() => setUploadingFleet(true)}>Загрузить парк</Button>}>
        Загрузите файл парка, чтобы увидеть борта на карте.
      </EmptyState>
    )
  else
    body = (
      <>
        <SideHead title="Парки БВС" sub="Выберите парк, чтобы посмотреть экземпляры." />
        <ul className="list">
          {fleets.data.map((item) => (
            <li key={item.id}>
              <button type="button" className="item" onClick={() => openFleet(item.id)}>
                <span className="body-col">
                  <span className="t">{item.name}</span>
                  <span className="m">{item.location_name ?? `${item.location_lat.toFixed(3)}, ${item.location_lon.toFixed(3)}`}</span>
                </span>
                <StatusBadge tone={item.status === "Корректна" ? "info" : "bad"}>
                  {item.ready_count}/{item.total} готовы
                </StatusBadge>
              </button>
            </li>
          ))}
        </ul>
      </>
    )

  const [showModels, setShowModels] = useState(false)

  return (
    <>
      <FleetLayer
        fleets={fleets.data ?? []}
        fleet={fleet.data ?? null}
        selected={inventory ?? null}
        onSelectFleet={openFleet}
        onSelectInstance={openInstance}
      />
      <Sidebar
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => setShowModels(!showModels)} aria-expanded={showModels}>
              Справочник моделей
            </Button>
            <Button size="sm" icon="upload" className="push" onClick={() => setUploadingFleet(true)}>
              Загрузить парк
            </Button>
          </>
        }
      >
        <CatalogTabs />
        {showModels ? (
          <div className="section">
            <div className="section-head">
              <span className="cap">Справочник моделей</span>
              <Button variant="ghost" size="sm" onClick={() => setShowModels(false)}>
                Скрыть
              </Button>
            </div>
            {(models.data ?? []).map((model) => (
              <div key={model.key} className="card flat section">
                <b>{model.name}</b>
                <KeyValue rows={modelRows(model)} />
              </div>
            ))}
          </div>
        ) : (
          body
        )}
      </Sidebar>
      {environments.data?.length === 0 ? (
        <OverlaySlot>
          <div className="card empty-map">
            <span style={{ color: "var(--accent)" }}>
              <Icon name="map" size={40} />
            </span>
            <div className="title">Карта пуста</div>
            <div className="muted sm">Загрузите обстановку — ВПП, площадки, разрешенное пространство, БПЗ и препятствия — из GeoJSON или KML.</div>
            <Button variant="primary" size="lg" onClick={() => setUploadingEnv(true)}>
              Загрузить данные
            </Button>
          </div>
        </OverlaySlot>
      ) : null}
      {uploadingFleet ? <UploadFleetModal onClose={() => setUploadingFleet(false)} onUploaded={(summary) => openFleet(summary.id)} /> : null}
      {uploadingEnv ? (
        <UploadEnvironmentModal
          onClose={() => setUploadingEnv(false)}
          onUploaded={(summary) => {
            setUploadingEnv(false)
            navigate(`/catalog/new?env=${summary.id}`)
          }}
          onOpen={(summary) => navigate(`/environments/${summary.id}`)}
        />
      ) : null}
    </>
  )
}
