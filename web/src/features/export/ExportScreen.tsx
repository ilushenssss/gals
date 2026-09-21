/**
 * Экран 6 «Подтверждение и экспорт» (ЭКС.ФТ.5-9, ИНТ.ФТ.16-17).
 *
 * Единственный экран, которого нет в legacy-интерфейсе, — и один из двух, где
 * план разрешает потратить дизайнерские усилия.
 *
 * Скачивание — обычными ссылками `<a download>`, а не `fetch` + blob: имена
 * файлов кириллические и приходят в `Content-Disposition` по RFC 5987, blob
 * заставил бы придумывать имя на клиенте и терять его.
 */
import { useNavigate, useParams } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { ApiError, api, exportAllUrl, exportUrl } from "@/api/client"
import { LegendSlot, Sidebar, useStep } from "@/app/AppShell"
import { EnvironmentLayers } from "@/map/EnvironmentLayers"
import { PlanRoutes, UavLegend } from "@/map/PlanRoutes"
import { useToast } from "@/shared/ui/Toasts"

const NO_HIDDEN: ReadonlySet<string> = new Set()

/** ЭКС.ФТ.5: статус → цвет. «Выгружен» — зелёный с иконкой экспорта. */
function statusBadge(status: string) {
  if (status === "Подтвержден") return <span className="status-badge ok">Подтвержден</span>
  if (status === "Выгружен") return <span className="status-badge ok">↧ Выгружен</span>
  if (status === "Проверен") return <span className="status-badge info">Проверен</span>
  return <span className="status-badge draft">{status}</span>
}

export function ExportScreen() {
  const { taskId, planId } = useParams()
  const navigate = useNavigate()
  const toast = useToast()
  const queryClient = useQueryClient()

  const task = useQuery({
    queryKey: ["task", taskId],
    queryFn: () => api.getTask(taskId!),
    enabled: Boolean(taskId),
  })
  const environment = useQuery({
    queryKey: ["environment", task.data?.environment_id],
    queryFn: () => api.getEnvironment(task.data!.environment_id),
    enabled: Boolean(task.data?.environment_id),
  })
  const plan = useQuery({
    queryKey: ["plan", planId],
    queryFn: () => api.getPlan(planId!),
    enabled: Boolean(planId),
  })
  const report = useQuery({
    queryKey: ["safety", planId],
    queryFn: () => api.latestSafetyReport(planId!),
    enabled: Boolean(planId),
  })

  const confirm = useMutation({
    mutationFn: () => api.confirmPlan(planId!),
    onSuccess: (summary) => {
      queryClient.invalidateQueries({ queryKey: ["plan", planId] })
      queryClient.invalidateQueries({ queryKey: ["plans", taskId] })
      toast(`План подтверждён: версия ${summary.version}`)
    },
    onError: (error: Error) => {
      // ЭКС.ФТ.9: подтвердил кто-то другой — показываем его имя и
      // подтягиваем актуальный статус, не повторяя подтверждение.
      if (error instanceof ApiError && error.status === 409) {
        queryClient.invalidateQueries({ queryKey: ["plan", planId] })
      }
      toast(error.message, true)
    },
  })

  const detail = plan.data ?? null
  const passed = report.data?.status === "Пройдена" && report.data.plan_id === planId
  const confirmed = detail?.status === "Подтвержден" || detail?.status === "Выгружен"
  const uavIds = detail ? [...new Set(detail.sorties.map((s) => s.uav_id))] : []

  const sidebar = (
    <>
      <a
        href="#"
        className="back-link"
        onClick={(event) => {
          event.preventDefault()
          navigate(`/tasks/${taskId}/plans/${planId}/safety`)
        }}
      >
        ← К проверке
      </a>
      <p className="sb-title">Подтверждение и экспорт</p>
      <p className="sb-sub">
        {task.data?.name ?? "…"}
        {detail ? ` · план версии ${detail.version}` : ""}
      </p>
      {detail ? statusBadge(detail.status) : null}

      {detail?.confirmed_by ? (
        <dl className="counts">
          <dt>Подтвердил</dt>
          <dd>{detail.confirmed_by}</dd>
          <dt>Когда</dt>
          <dd>
            {detail.confirmed_at
              ? new Date(detail.confirmed_at).toLocaleString("ru-RU")
              : "—"}
          </dd>
        </dl>
      ) : null}

      {!confirmed ? (
        <>
          {!passed ? (
            <div className="warning-box">
              Подтверждение доступно только для плана, прошедшего проверку безопасности без
              нарушений (ЭКС.ФТ.2).
            </div>
          ) : null}
          <button
            className="btn primary"
            style={{ width: "100%" }}
            disabled={!passed || confirm.isPending}
            onClick={() => confirm.mutate()}
          >
            {confirm.isPending ? "Подтверждение…" : "Подтвердить"}
          </button>
        </>
      ) : (
        <>
          <h3 className="sb-section">Экспорт по БВС</h3>
          <ul className="export-list">
            {uavIds.map((uavId) => (
              <li key={uavId}>
                <span className="export-uav">{uavId}</span>
                <a className="btn" href={exportUrl(planId!, "kml", uavId)} download>
                  KML
                </a>
                <a className="btn" href={exportUrl(planId!, "geojson", uavId)} download>
                  GeoJSON
                </a>
              </li>
            ))}
          </ul>
          <a
            className="btn primary export-all"
            href={exportAllUrl(planId!)}
            download
            onClick={() => {
              // Первая выгрузка переводит план в «Выгружен» — обновляем
              // карточку сразу после того, как браузер заберёт файл.
              window.setTimeout(
                () => queryClient.invalidateQueries({ queryKey: ["plan", planId] }),
                800,
              )
            }}
          >
            Скачать все
          </a>
          <p className="hint" style={{ marginTop: 10 }}>
            Файлы в WGS-84, время в ISO 8601 UTC. Загрузка на борт выполняется штатным
            программным обеспечением БВС.
          </p>
        </>
      )}
    </>
  )

  useStep(4, [0, 1, 2, 3], (index) => {
        if (index === 0 && task.data) navigate(`/environments/${task.data.environment_id}`)
        if (index === 1) navigate(`/tasks/${taskId}`)
        if (index === 2) navigate(`/tasks/${taskId}/plans/${planId}`)
        if (index === 3) navigate(`/tasks/${taskId}/plans/${planId}/safety`)
      })

  return (
    <>
      <EnvironmentLayers environment={environment.data ?? null} hidden={NO_HIDDEN} dimmed />
      <PlanRoutes plan={detail} area={task.data?.area ?? null} />
      <Sidebar>{sidebar}</Sidebar>
      <LegendSlot>
        <UavLegend uavIds={uavIds} />
      </LegendSlot>
    </>
  )
}
