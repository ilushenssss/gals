/**
 * Этап 1 «Обстановка» (ИНТ.ФТ.5–7): слои на карте, сводка, ошибки проверки.
 *
 * Два входа. `/environments/:id` — из каталога при создании задачи (или
 * чтобы посмотреть ошибки загруженного файла); «Далее» ведет к форме
 * задачи. `/tasks/:taskId/environment` — этап уже созданной задачи: та же
 * сводка только для просмотра, «Далее» возвращает к задаче.
 */
import { useState } from "react"
import { Link, useNavigate, useParams } from "react-router-dom"
import { useQuery } from "@tanstack/react-query"
import { api } from "@/api/client"
import type { EnvironmentDetail } from "@/api/client"
import { LegendSlot, Sidebar, useHeaderContext, useStep } from "@/app/AppShell"
import { EnvironmentLayers, layerCounts } from "@/map/EnvironmentLayers"
import { EnvironmentLegend, LegendBox } from "@/map/Legend"
import { Button } from "@/shared/ui/Button"
import { Banner, KeyValue, SideHead } from "@/shared/ui/Display"
import { Icon } from "@/shared/ui/Icon"
import { ErrorState, Skeleton } from "@/shared/ui/States"
import { StatusBadge } from "@/shared/ui/StatusBadge"
import { formatDateTime } from "@/shared/format"
import { useToggleSet } from "@/shared/useToggleSet"
import { UploadEnvironmentModal } from "@/features/catalog/UploadModals"
import { STEP, useTaskChrome, useTaskFlow } from "@/features/task/useTaskFlow"

const LAYER_LABEL: Record<string, string> = {
  launch_site: "ВПП",
  reserve_site: "Резервная площадка",
  airspace: "Пространство",
  no_fly: "БПЗ",
  obstacle: "Препятствие",
}

function Summary({ detail }: { detail: EnvironmentDetail }) {
  const counts = detail.counts as Record<string, number>
  const invalidBy = detail.errors.reduce<Record<string, number>>((acc, e) => ({ ...acc, [e.layer]: (acc[e.layer] ?? 0) + 1 }), {})
  const row = (key: string, label: string) =>
    [label, <span key={key}><span className="mono">{counts[key] ?? 0}</span>{invalidBy[key] ? <span style={{ color: "var(--danger)" }}> · {invalidBy[key]} с ошибками</span> : null}</span>] as const
  return (
    <>
      <KeyValue
        rows={[
          row("launch_site", "ВПП"),
          row("reserve_site", "Резервные площадки"),
          row("airspace", "Разрешенное пространство"),
          row("no_fly", "Бесполетные зоны"),
          row("obstacle", "Высотные препятствия"),
        ]}
      />
      {detail.errors.length ? (
        <div className="section">
          <div className="cap">Ошибки проверки · {detail.errors.length}</div>
          <ul className="list">
            {detail.errors.map((issue, index) => (
              <li key={index} className="viol hl" style={{ margin: 0, flexDirection: "column", gap: 2 }}>
                <span style={{ display: "flex", gap: 6, alignItems: "center" }}>
                  <StatusBadge tone="bad">{LAYER_LABEL[issue.layer] ?? issue.layer}</StatusBadge>
                  <b>
                    №{issue.feature_index + 1}
                    {issue.name ? ` «${issue.name}»` : ""}
                  </b>
                </span>
                <span>{issue.message}</span>
              </li>
            ))}
          </ul>
          <p className="muted sm">Объекты с ошибками обведены на карте красным. Исправьте файл и загрузите его заново.</p>
        </div>
      ) : null}
    </>
  )
}

/** Экран без задачи: из каталога при создании новой. */
export function EnvironmentScreen() {
  const { environmentId } = useParams()
  const navigate = useNavigate()
  const [uploading, setUploading] = useState(false)
  const [hidden, toggle] = useToggleSet()
  const environment = useQuery({
    queryKey: ["environment", environmentId],
    queryFn: () => api.getEnvironment(environmentId!),
    enabled: Boolean(environmentId),
  })
  const detail = environment.data ?? null
  const ok = detail?.status === "Корректна"

  useHeaderContext({ title: "Новая задача", exitTo: "/catalog/new", exitLabel: "Каталог" })
  useStep(STEP.environment, ok ? [STEP.environment] : [], (index) => {
    if (index === STEP.task && ok) navigate(`/environments/${environmentId}/tasks/new`)
  })

  return (
    <>
      <EnvironmentLayers environment={detail} hidden={hidden} />
      {detail ? (
        <LegendSlot>
          <LegendBox>
            <EnvironmentLegend counts={layerCounts(detail)} hidden={hidden} onToggle={toggle} />
          </LegendBox>
        </LegendSlot>
      ) : null}
      <Sidebar
        footer={
          detail ? (
            <>
              <Button variant="ghost" icon="upload" onClick={() => setUploading(true)}>
                {ok ? "Другой файл" : "Загрузить заново"}
              </Button>
              <Button
                variant="primary"
                className="push"
                disabled={!ok}
                title={ok ? undefined : "Недоступно, пока в обстановке есть ошибки"}
                onClick={() => navigate(`/environments/${detail.id}/tasks/new`)}
              >
                Далее: Задача
              </Button>
            </>
          ) : null
        }
      >
        <Link className="back" to={`/catalog/new${detail ? `?env=${detail.id}` : ""}`}>
          <Icon name="back" size={14} />
          Новая задача
        </Link>
        {environment.isPending ? (
          <Skeleton />
        ) : environment.isError ? (
          <ErrorState error={environment.error} onRetry={() => environment.refetch()} />
        ) : detail ? (
          <>
            <SideHead title={detail.name} badge={<StatusBadge status={detail.status} />} sub={`Загружена ${formatDateTime(detail.uploaded_at)}`} />
            <Summary detail={detail} />
          </>
        ) : null}
      </Sidebar>
      {uploading ? (
        <UploadEnvironmentModal
          onClose={() => setUploading(false)}
          onUploaded={(summary) => {
            setUploading(false)
            navigate(`/environments/${summary.id}`)
          }}
          onOpen={(summary) => {
            setUploading(false)
            navigate(`/environments/${summary.id}`)
          }}
        />
      ) : null}
    </>
  )
}

/** Этап «Обстановка» внутри задачи — только просмотр. */
export function TaskEnvironmentScreen() {
  const { taskId } = useParams()
  const navigate = useNavigate()
  const [hidden, toggle] = useToggleSet()
  const flow = useTaskFlow(taskId)
  useTaskChrome(flow, STEP.environment, taskId)
  const detail = flow.environment.data ?? null

  return (
    <>
      <EnvironmentLayers environment={detail} hidden={hidden} />
      {detail ? (
        <LegendSlot>
          <LegendBox>
            <EnvironmentLegend counts={layerCounts(detail)} hidden={hidden} onToggle={toggle} />
          </LegendBox>
        </LegendSlot>
      ) : null}
      <Sidebar
        footer={
          <Button variant="primary" block iconAfter="next" onClick={() => navigate(`/tasks/${taskId}`)}>
            К задаче
          </Button>
        }
      >
        {flow.environment.isPending ? (
          <Skeleton />
        ) : flow.environment.isError ? (
          <ErrorState error={flow.environment.error} onRetry={() => flow.environment.refetch()} />
        ) : detail ? (
          <>
            <SideHead title={detail.name} badge={<StatusBadge status={detail.status} />} sub={`Загружена ${formatDateTime(detail.uploaded_at)}`} />
            <Banner kind="info">Обстановка задачи не меняется: по ней считаются планы. Для другой обстановки создайте новую задачу.</Banner>
            <Summary detail={detail} />
          </>
        ) : null}
      </Sidebar>
    </>
  )
}
