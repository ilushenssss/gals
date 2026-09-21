/**
 * Панель фонового расчёта (ПЛН.ФТ.5) — единственный экран, которого в legacy
 * не было вовсе, и один из двух, куда план разрешает потратить дизайн.
 *
 * Опрос раз в 2 с: расчёт идёт до 30 минут, обновление раз в пару секунд
 * заведомо достаточно, а SSE ради этого заводить незачем.
 */
import { useQuery } from "@tanstack/react-query"
import { api, type JobInfo } from "@/api/client"

const ACTIVE = new Set(["В очереди", "Выполняется"])

export function isActive(job: JobInfo | null | undefined): boolean {
  return Boolean(job && ACTIVE.has(job.status))
}

function tone(status: string): string {
  if (status === "Завершен") return "ok"
  if (status === "Ошибка") return "bad"
  if (status === "Отменен" || status === "Остановлен по лимиту времени") return "warn"
  return "info"
}

/**
 * Опрос статуса работы. Останавливается сам, когда работа завершилась.
 *
 * ``refetchIntervalInBackground`` включён намеренно: по умолчанию TanStack
 * Query замораживает интервал в неактивной вкладке, и индикатор
 * получасового расчёта застывал бы на том проценте, который оператор видел
 * перед тем, как переключиться на другую вкладку, — а именно так с
 * получасовым расчётом и работают.
 */
export function useJob(jobId: string | null) {
  return useQuery({
    queryKey: ["plan-job", jobId],
    queryFn: () => api.getJob(jobId!),
    enabled: Boolean(jobId),
    refetchInterval: (query) => (isActive(query.state.data) ? 2000 : false),
    refetchIntervalInBackground: true,
  })
}

/**
 * Активная работа по задаче. Нужна для переподключения после перезагрузки
 * вкладки: 30-минутный расчёт обязан пережить F5, иначе «фоновый расчёт»
 * ломается на первом же обновлении страницы.
 */
export function useActiveJob(taskId: string | null) {
  return useQuery({
    queryKey: ["plan-jobs", taskId],
    queryFn: () => api.listJobs(taskId!),
    enabled: Boolean(taskId),
    select: (jobs) => jobs.find(isActive) ?? jobs[0] ?? null,
    // refetchInterval получает несобранные данные запроса (весь список), а не
    // результат select — поэтому активность ищем по массиву.
    refetchInterval: (query) => (query.state.data?.some(isActive) ? 2000 : false),
    refetchIntervalInBackground: true,
  })
}

export function JobProgress({
  job,
  onCancel,
  cancelling,
}: {
  job: JobInfo
  onCancel: () => void
  cancelling: boolean
}) {
  const running = isActive(job)
  return (
    <div className="job-panel">
      <div className="job-head">
        <span className={`status-badge ${tone(job.status)}`}>{job.status}</span>
        {running ? <span className="job-percent">{job.progress}%</span> : null}
      </div>
      {job.stage ? <p className="job-stage">{job.stage}</p> : null}
      <div className="job-bar">
        <div
          className={running ? "job-bar-fill running" : "job-bar-fill"}
          style={{ width: `${job.progress}%` }}
        />
      </div>
      {job.error ? <div className="warning-box">{job.error}</div> : null}
      {running ? (
        <button
          className="btn"
          style={{ width: "100%", marginTop: 10 }}
          disabled={cancelling || job.cancel_requested}
          onClick={onCancel}
        >
          {job.cancel_requested ? "Отмена запрошена…" : "Отменить расчёт"}
        </button>
      ) : null}
    </div>
  )
}
