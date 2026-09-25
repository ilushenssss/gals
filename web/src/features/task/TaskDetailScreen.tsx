/**
 * Карточка задачи (ЗАД.ФТ.11) и запуск расчета (ПЛН.ФТ.5).
 *
 * Если задачу изменили после расчета текущего плана, карточка говорит об
 * этом прямо: план посчитан по прежним параметрам, этапы дальше нужно
 * пройти заново (ИНТ.ФТ.20). Идущий расчет не запускается второй раз —
 * карточка ведет к нему.
 */
import { useMemo } from "react"
import { Link, useNavigate, useParams } from "react-router-dom"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { api, isJobOutcome } from "@/api/client"
import { Sidebar } from "@/app/AppShell"
import { EnvironmentLayers } from "@/map/EnvironmentLayers"
import { AreaLayer } from "@/map/AreaLayer"
import { ConflictLayer } from "@/map/ConflictLayer"
import { Button, buttonClass } from "@/shared/ui/Button"
import { Banner, KeyValue, SideHead } from "@/shared/ui/Display"
import { Icon } from "@/shared/ui/Icon"
import { ErrorState, Skeleton } from "@/shared/ui/States"
import { StatusBadge } from "@/shared/ui/StatusBadge"
import { useToast } from "@/shared/ui/Toasts"
import { formatDateTime, formatNumber, zoneLabel } from "@/shared/format"
import { isActive, useActiveJob } from "@/features/planning/JobProgress"
import { STEP, useTaskChrome, useTaskFlow } from "./useTaskFlow"
import { areaConflicts } from "./areaConflicts"

const NO_HIDDEN: ReadonlySet<string> = new Set()
const M = ({ children }: { children: React.ReactNode }) => <span className="mono">{children}</span>

export function TaskDetailScreen() {
  const { taskId } = useParams()
  const navigate = useNavigate()
  const toast = useToast()
  const queryClient = useQueryClient()
  const flow = useTaskFlow(taskId)
  useTaskChrome(flow, STEP.task, taskId)
  const job = useActiveJob(taskId ?? null)
  const running = isActive(job.data)

  const calculate = useMutation({
    mutationFn: () => api.createPlan(taskId!),
    onSuccess: ({ plan, job: started }) => {
      queryClient.invalidateQueries({ queryKey: ["plans", taskId] })
      queryClient.invalidateQueries({ queryKey: ["plan-jobs", taskId] })
      queryClient.invalidateQueries({ queryKey: ["task", taskId] })
      if (plan) navigate(`/tasks/${taskId}/plans/${plan.id}`)
      else if (started) navigate(`/tasks/${taskId}/plans`)
    },
    onError: (error: Error) => {
      // Исход без плана (см. isJobOutcome) показывает экран планирования.
      if (!isJobOutcome(error)) return toast(error.message, true)
      queryClient.invalidateQueries({ queryKey: ["plan-jobs", taskId] })
      navigate(`/tasks/${taskId}/plans`)
    },
  })

  const detail = flow.task.data
  const conflicts = useMemo(() => areaConflicts(flow.environment.data, detail?.area), [flow.environment.data, detail?.area])
  const conflictZones = useMemo(() => conflicts.map((c) => c.geometry), [conflicts])
  const editable = detail?.status !== "Подтверждена"
  const latest = flow.latest

  const footer = detail ? (
    <>
      {editable ? <Link className={buttonClass({})} to={`/tasks/${detail.id}/edit`}>Редактировать</Link> : null}
      {latest ? (
        <Link className={buttonClass({ variant: "ghost" })} to={`/tasks/${detail.id}/plans/${latest.id}`}>
          План v{latest.version}
        </Link>
      ) : null}
      {running ? (
        <Link className={buttonClass({ variant: "primary", extra: "push" })} to={`/tasks/${detail.id}/plans`}>
          <span className="spin" />
          Идет расчет
        </Link>
      ) : (
        <Button variant="primary" className="push" busy={calculate.isPending} onClick={() => calculate.mutate()}>
          {latest ? "Пересчитать" : "Рассчитать"}
        </Button>
      )}
    </>
  ) : null

  return (
    <>
      <EnvironmentLayers environment={flow.environment.data ?? null} hidden={NO_HIDDEN} dimmed />
      <AreaLayer area={detail?.area ?? null} boundsKey={detail?.id ?? null} />
      <ConflictLayer zones={conflictZones} />
      <Sidebar footer={footer}>
        <Link className="back" to="/catalog/history">
          <Icon name="back" size={14} />
          История задач
        </Link>
        {flow.task.isPending ? (
          <Skeleton />
        ) : flow.task.isError ? (
          <ErrorState error={flow.task.error} onRetry={() => flow.task.refetch()} />
        ) : detail ? (
          <>
            <SideHead
              title={detail.name}
              badge={<StatusBadge status={detail.status} />}
              sub={`Изменена ${formatDateTime(detail.updated_at)}${detail.updated_by ? ` · ${detail.updated_by}` : ""} · версия ${detail.version}`}
            />
            {flow.planStale && latest ? (
              <Banner kind="warn">
                <b>Задача изменена после расчета.</b> План версии {latest.version} рассчитан по прежним параметрам — пересчитайте его и пройдите проверку заново.
              </Banner>
            ) : null}
            <KeyValue
              rows={[
                ["Обстановка", <Link to={`/tasks/${detail.id}/environment`}>{detail.environment_name}</Link>],
                ["Парк", detail.fleet_name],
                ["Тип съемки", detail.survey_type],
                ["GSD", <M>{formatNumber(detail.gsd_cm)} см/пикс</M>],
                ["Дата", <M>{new Date(detail.work_date).toLocaleDateString("ru-RU")}</M>],
                [
                  "Окно работ",
                  detail.window_start || detail.window_end ? (
                    <>
                      <M>
                        {detail.window_start?.slice(0, 5) ?? "—"}–{detail.window_end?.slice(0, 5) ?? "—"}
                      </M>{" "}
                      <span className="muted">{zoneLabel(detail.timezone)}</span>
                    </>
                  ) : (
                    "световой день"
                  ),
                ],
                ["Ветер", detail.wind_speed_ms != null ? <M>{formatNumber(detail.wind_speed_ms)} м/с</M> : "—"],
                ["Облачность", detail.cloud_cover_pct != null ? <M>{detail.cloud_cover_pct} %</M> : "—"],
                ["Критерий", detail.criterion_mode === "Компромисс" ? <>Компромисс, α = <M>{formatNumber(detail.criterion_alpha, 2)}</M></> : detail.criterion_mode],
              ]}
            />
            {detail.daylight_warning ? <Banner kind="warn">{detail.daylight_warning}</Banner> : null}
            {conflicts.length ? (
              <Banner kind="warn">
                Область пересекает БПЗ {conflicts.map((c) => `«${c.name}»`).join(", ")} — участок в зоне сниматься не будет.
              </Banner>
            ) : null}
            {!editable ? <Banner kind="info">Задача подтверждена и больше не редактируется.</Banner> : null}
          </>
        ) : null}
      </Sidebar>
    </>
  )
}
