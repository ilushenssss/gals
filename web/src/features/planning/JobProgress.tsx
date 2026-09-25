/**
 * Панель фонового расчета (ПЛН.ФТ.5, ИНТ.ФТ.10) и ее исходы.
 *
 * Опрос раз в 2 с: расчет идет до 30 минут, обновления раз в пару секунд
 * заведомо достаточно, а SSE ради этого заводить незачем. Список стадий
 * здесь не рисуется: внутри расчета стадии повторяются для каждого
 * кандидата «модель + камера», и фиксированный перечень врал бы о том, что
 * осталось. Показываются текущая стадия, процент и прошедшее время.
 */
import { useEffect, useState } from "react"
import type { ReactNode } from "react"
import { useQuery } from "@tanstack/react-query"
import { api } from "@/api/client"
import type { JobInfo } from "@/api/client"
import { Button } from "@/shared/ui/Button"
import { Banner, ProgressBar } from "@/shared/ui/Display"
import { StatusBadge } from "@/shared/ui/StatusBadge"

const ACTIVE = new Set(["В очереди", "Выполняется"])

export function isActive(job: JobInfo | null | undefined): boolean {
  return Boolean(job && ACTIVE.has(job.status))
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
 * Последняя работа расчета плана по задаче. Нужна для переподключения после
 * перезагрузки вкладки: 30-минутный расчет обязан пережить F5.
 */
export function useActiveJob(taskId: string | null, kind: "plan" | "safety" = "plan") {
  return useQuery({
    queryKey: ["plan-jobs", taskId],
    queryFn: () => api.listJobs(taskId!),
    enabled: Boolean(taskId),
    select: (jobs) => {
      const own = jobs.filter((job) => job.kind === kind)
      return own.find(isActive) ?? own[0] ?? null
    },
    // refetchInterval получает несобранные данные запроса (весь список), а не
    // результат select — поэтому активность ищем по массиву.
    refetchInterval: (query) => (query.state.data?.some(isActive) ? 2000 : false),
    refetchIntervalInBackground: true,
  })
}

function useElapsed(since: string | null | undefined, running: boolean): string | null {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!running) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [running])
  if (!since) return null
  const seconds = Math.max(0, Math.round((now - new Date(since).getTime()) / 1000))
  const m = Math.floor(seconds / 60)
  return m ? `${m} мин ${seconds % 60} с` : `${seconds} с`
}

export function JobProgress({
  job,
  onCancel,
  cancelling,
  title,
  cancelLabel = "Отменить расчет",
}: {
  job: JobInfo
  onCancel: () => void
  cancelling: boolean
  title?: string
  cancelLabel?: string
}) {
  const running = isActive(job)
  const queued = job.status === "В очереди"
  const elapsed = useElapsed(job.started_at ?? job.queued_at, running)
  // Автопересчет БЕЗ.ФТ.3 идет внутри работы проверки — показываем его
  // отдельным (желтым) статусом, как в отчете.
  const recalculating = Boolean(job.stage?.startsWith("В процессе автоматического пересчета"))
  return (
    <div className="card section" style={{ padding: 16, gap: 10 }} aria-live="polite">
      <div className="section-head">
        {recalculating ? <StatusBadge tone="warn">Автопересчет</StatusBadge> : <StatusBadge status={job.status} />}
        {running && elapsed ? <span className="mono muted sm">{elapsed}</span> : null}
      </div>
      <div className="section-head" style={{ alignItems: "baseline" }}>
        <span style={{ fontWeight: 600, fontSize: 15 }}>{queued ? "Ожидает свободного исполнителя" : job.stage || title || "Расчет"}</span>
        {running && !queued ? (
          <span className="mono" style={{ fontSize: 22, fontWeight: 700 }}>
            {job.progress}%
          </span>
        ) : null}
      </div>
      {running ? <ProgressBar value={queued ? 100 : job.progress} running label="Прогресс расчета" /> : null}
      {job.error ? <Banner kind={job.status === "Ошибка" ? "bad" : "warn"}>{job.error}</Banner> : null}
      {running ? (
        <Button variant="danger" block busy={cancelling} disabled={job.cancel_requested} onClick={onCancel}>
          {job.cancel_requested ? "Отмена запрошена…" : queued ? "Убрать из очереди" : cancelLabel}
        </Button>
      ) : null}
    </div>
  )
}

/** Итог завершившейся без плана работы — что случилось и что делать дальше. */
export function JobOutcome({ job, actions }: { job: JobInfo; actions: ReactNode }) {
  const text: Record<string, { title: string; body: string }> = {
    "Остановлен по лимиту времени": {
      title: "Расчет не уложился в лимит времени",
      body: "Лучшее найденное решение пока не сохраняется. Уменьшите область облета или ослабьте ограничения задачи и запустите снова.",
    },
    "Ошибка": {
      title: "Расчет завершился ошибкой",
      body: "Задача и прежние версии плана не пострадали. Если причина — перезапуск сервиса, достаточно запустить расчет снова.",
    },
    "Отменен": {
      title: "Расчет отменен",
      body: "Предыдущие версии плана, если они есть, остаются доступны.",
    },
  }
  // Невыполнимая задача (ПЛН.ФТ.10) — тоже «Ошибка», но совет про
  // перезапуск сервиса тут вводит в заблуждение: менять нужно задачу.
  const info =
    job.error_code === "infeasible"
      ? { title: "Расчет невозможен", body: "Задача и прежние версии плана не пострадали. Измените параметры задачи по причине ниже и запустите снова." }
      : text[job.status]
  if (!info) return null
  return (
    <div className="card section" style={{ padding: 16, gap: 10 }}>
      <StatusBadge status={job.status} />
      <div style={{ fontWeight: 700, fontSize: 15 }}>{info.title}</div>
      <div className="muted sm">{info.body}</div>
      {job.error ? <div className="sm">{job.error}</div> : null}
      <div style={{ display: "flex", gap: 8 }}>{actions}</div>
    </div>
  )
}
