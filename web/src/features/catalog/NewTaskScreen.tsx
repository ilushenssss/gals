/**
 * Каталог, вкладка «Новая задача»: выбор обстановки и парка, затем форма
 * параметров. Выбранная обстановка сразу видна на карте.
 *
 * Обстановку с ошибками выбрать нельзя (ее можно открыть и посмотреть
 * ошибки), парк дальше 150 км от района работ — тоже: борта физически не
 * долетят до области. Расстояние считается здесь только для подсказки;
 * окончательное решение за бэкендом при сохранении задачи.
 */
import { useMemo, useState } from "react"
import { Link, useNavigate, useSearchParams } from "react-router-dom"
import { useQuery } from "@tanstack/react-query"
import { api } from "@/api/client"
import type { EnvironmentDetail, FleetSummary } from "@/api/client"
import { LegendSlot, Sidebar } from "@/app/AppShell"
import { EnvironmentLayers, layerCounts } from "@/map/EnvironmentLayers"
import { EnvironmentLegend, LegendBox } from "@/map/Legend"
import { Button } from "@/shared/ui/Button"
import { Skeleton } from "@/shared/ui/States"
import { StatusBadge } from "@/shared/ui/StatusBadge"
import { boundsOf, distanceKm } from "@/shared/geo"
import { formatDateTime } from "@/shared/format"
import { useToggleSet } from "@/shared/useToggleSet"
import { CatalogTabs } from "./CatalogTabs"
import { UploadEnvironmentModal, UploadFleetModal } from "./UploadModals"

const FLEET_LIMIT_KM = 150

function environmentCenter(environment: EnvironmentDetail | undefined): [number, number] | null {
  if (!environment) return null
  const features = Object.values(environment.layers as Record<string, Array<{ geometry: unknown }>>).flat()
  const b = boundsOf(features.map((f) => f.geometry))
  return b ? [(b.minLat + b.maxLat) / 2, (b.minLon + b.maxLon) / 2] : null
}

export function NewTaskScreen() {
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const envId = params.get("env")
  const fleetId = params.get("fleet")
  const [uploadingEnv, setUploadingEnv] = useState(false)
  const [uploadingFleet, setUploadingFleet] = useState(false)
  const [hidden, toggle] = useToggleSet()

  const environments = useQuery({ queryKey: ["environments"], queryFn: api.listEnvironments })
  const fleets = useQuery({ queryKey: ["fleets"], queryFn: api.listFleets })
  const environment = useQuery({ queryKey: ["environment", envId], queryFn: () => api.getEnvironment(envId!), enabled: Boolean(envId) })

  const select = (key: "env" | "fleet", value: string) => {
    const next = new URLSearchParams(params)
    next.set(key, value)
    setParams(next, { replace: true })
  }

  const center = useMemo(() => environmentCenter(environment.data), [environment.data])
  const distanceOf = (fleet: FleetSummary) => (center ? distanceKm(center[0], center[1], fleet.location_lat, fleet.location_lon) : null)

  const selectedEnvOk = environments.data?.find((e) => e.id === envId)?.status === "Корректна"
  const selectedFleet = fleets.data?.find((f) => f.id === fleetId)
  const fleetDistance = selectedFleet ? distanceOf(selectedFleet) : null
  const fleetOk = Boolean(selectedFleet && selectedFleet.ready_count > 0 && (fleetDistance == null || fleetDistance <= FLEET_LIMIT_KM))
  const canProceed = Boolean(envId && selectedEnvOk && fleetId && fleetOk)

  return (
    <>
      <EnvironmentLayers environment={environment.data ?? null} hidden={hidden} />
      {environment.data ? (
        <LegendSlot>
          <LegendBox>
            <EnvironmentLegend counts={layerCounts(environment.data)} hidden={hidden} onToggle={toggle} />
          </LegendBox>
        </LegendSlot>
      ) : null}
      <Sidebar
        footer={
          <Button
            variant="primary"
            block
            iconAfter="next"
            disabled={!canProceed}
            onClick={() => navigate(`/environments/${envId}/tasks/new?fleet=${fleetId}`)}
          >
            Далее: параметры задачи
          </Button>
        }
      >
        <CatalogTabs />
        <div className="section">
          <div className="section-head">
            <span className="cap">1 · Обстановка</span>
            <Button variant="ghost" size="sm" icon="plus" onClick={() => setUploadingEnv(true)}>
              Загрузить
            </Button>
          </div>
          {environments.isPending ? (
            <Skeleton lines={2} />
          ) : environments.data?.length ? (
            <div className="list" role="radiogroup" aria-label="Обстановка">
              {environments.data.map((env) => {
                const ok = env.status === "Корректна"
                const total = Object.values(env.counts as Record<string, number>).reduce((a, b) => a + b, 0)
                return (
                  <label key={env.id} className={env.id === envId ? "item sel" : "item"}>
                    <input type="radio" name="env" checked={env.id === envId} onChange={() => select("env", env.id)} />
                    <span className="body-col">
                      <span className="t">{env.name}</span>
                      <span className="m">
                        {total} объектов · {formatDateTime(env.uploaded_at)}
                      </span>
                      {!ok ? (
                        <Link className="m" to={`/environments/${env.id}`} style={{ color: "var(--danger)" }}>
                          Ошибок: {env.errors.length} — открыть и посмотреть
                        </Link>
                      ) : null}
                    </span>
                    <StatusBadge status={env.status} />
                  </label>
                )
              })}
            </div>
          ) : (
            <p className="muted sm">Обстановок нет — загрузите первую.</p>
          )}
          {envId && !selectedEnvOk && environments.data ? (
            <span className="err">Обстановку с ошибками нельзя выбрать для задачи — исправьте файл и загрузите заново.</span>
          ) : null}
        </div>

        <div className="section">
          <div className="section-head">
            <span className="cap">2 · Парк</span>
            <Button variant="ghost" size="sm" icon="plus" onClick={() => setUploadingFleet(true)}>
              Загрузить
            </Button>
          </div>
          {fleets.isPending ? (
            <Skeleton lines={2} />
          ) : fleets.data?.length ? (
            <div className="list" role="radiogroup" aria-label="Парк">
              {fleets.data.map((fleet) => {
                const distance = distanceOf(fleet)
                const far = distance != null && distance > FLEET_LIMIT_KM
                const empty = fleet.ready_count === 0
                return (
                  <label key={fleet.id} className={fleet.id === fleetId ? "item sel" : "item"}>
                    <input type="radio" name="fleet" checked={fleet.id === fleetId} onChange={() => select("fleet", fleet.id)} />
                    <span className="body-col">
                      <span className="t">{fleet.name}</span>
                      <span className="m">
                        {fleet.ready_count} из {fleet.total} готовы
                        {distance != null ? ` · ${Math.round(distance)} км до обстановки` : ""}
                      </span>
                      {far ? <span className="m">Дальше {FLEET_LIMIT_KM} км от района работ</span> : null}
                      {empty ? <span className="m">Нет готовых бортов</span> : null}
                    </span>
                    {far || empty ? <StatusBadge tone="draft">Не подходит</StatusBadge> : distance != null ? <StatusBadge tone="ok">Подходит</StatusBadge> : null}
                  </label>
                )
              })}
            </div>
          ) : (
            <p className="muted sm">Парков нет — загрузите первый.</p>
          )}
        </div>
      </Sidebar>
      {uploadingEnv ? (
        <UploadEnvironmentModal
          onClose={() => setUploadingEnv(false)}
          onUploaded={(summary) => {
            setUploadingEnv(false)
            select("env", summary.id)
          }}
          onOpen={(summary) => navigate(`/environments/${summary.id}`)}
        />
      ) : null}
      {uploadingFleet ? <UploadFleetModal onClose={() => setUploadingFleet(false)} onUploaded={(summary) => select("fleet", summary.id)} /> : null}
    </>
  )
}
