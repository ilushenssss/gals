/**
 * Сценарий одной задачи: данные, общие для экранов 1–6, и состояние
 * степпера (ИНТ.ФТ.2, ИНТ.ФТ.20).
 *
 * Этап считается пройденным по данным, а не по тому, на каком экране
 * оператор был: обстановка — есть (задача без корректной не создается),
 * задача — сохранена, планирование — есть план, проверка — отчет по этой
 * версии пройден или все нарушения приняты, экспорт — план подтвержден.
 *
 * «Нужно пройти заново»: задачу изменили после расчета. Правка возвращает
 * задачу в «Черновик» (ПЛН.ФТ.8), расчет переводит в «Рассчитана»; значит,
 * черновик при уже существующих планах — это правка после последнего
 * расчета, и планирование и всё после него пройдено по устаревшим данным.
 * По времени сравнивать нельзя: сам расчет обновляет `updated_at` задачи.
 */
import { useQuery } from "@tanstack/react-query"
import { useNavigate } from "react-router-dom"
import { api } from "@/api/client"
import type { PlanSummary } from "@/api/client"
import { useHeaderContext, useStep } from "@/app/AppShell"

export const STEP = { environment: 0, task: 1, planning: 2, safety: 3, export: 4 } as const

export function latestPlan(plans: PlanSummary[] | undefined): PlanSummary | null {
  if (!plans?.length) return null
  return plans.reduce((best, plan) => (plan.version > best.version ? plan : best))
}

export function useTaskFlow(taskId: string | undefined, planId?: string | undefined) {
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
  const plans = useQuery({
    queryKey: ["plans", taskId],
    queryFn: () => api.listPlans(taskId!),
    enabled: Boolean(taskId),
  })
  const latest = latestPlan(plans.data)
  const currentPlanId = planId ?? latest?.id ?? null
  const currentPlan = plans.data?.find((p) => p.id === currentPlanId) ?? null
  const report = useQuery({
    queryKey: ["safety", currentPlanId],
    queryFn: () => api.latestSafetyReport(currentPlanId!),
    enabled: Boolean(currentPlanId),
  })

  const planStale = Boolean(task.data?.status === "Черновик" && currentPlan)
  const reportForPlan = report.data && report.data.plan_id === currentPlanId ? report.data : null
  const safetyDone = Boolean(reportForPlan && (reportForPlan.status === "Пройдена" || reportForPlan.violations_acknowledged))
  const confirmed = currentPlan?.status === "Подтвержден" || currentPlan?.status === "Выгружен"

  const done: number[] = []
  if (task.data) done.push(STEP.environment, STEP.task)
  if (currentPlan) done.push(STEP.planning)
  if (safetyDone) done.push(STEP.safety)
  if (confirmed) done.push(STEP.export)
  const stale = planStale ? [STEP.planning, STEP.safety, STEP.export].filter((s) => done.includes(s)) : []

  return {
    task,
    environment,
    plans,
    report,
    latest,
    currentPlanId,
    currentPlan,
    planStale,
    safetyDone,
    confirmed,
    done,
    stale,
  }
}

/** Степпер и заголовок задачи в шапке для экранов внутри задачи. */
export function useTaskChrome(
  flow: ReturnType<typeof useTaskFlow>,
  active: number,
  taskId: string | undefined,
) {
  const navigate = useNavigate()
  const planId = flow.currentPlanId
  useHeaderContext(
    flow.task.data ? { title: flow.task.data.name, exitTo: "/catalog/history", exitLabel: "Каталог" } : null,
  )
  useStep(
    active,
    flow.done,
    (index) => {
      if (!taskId) return
      if (index === STEP.environment) navigate(`/tasks/${taskId}/environment`)
      if (index === STEP.task) navigate(`/tasks/${taskId}`)
      if (index === STEP.planning) navigate(planId ? `/tasks/${taskId}/plans/${planId}` : `/tasks/${taskId}/plans`)
      if (index === STEP.safety && planId) navigate(`/tasks/${taskId}/plans/${planId}/safety`)
      if (index === STEP.export && planId) navigate(`/tasks/${taskId}/plans/${planId}/export`)
    },
    flow.stale,
  )
}
