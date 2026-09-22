/**
 * Экран 5 «Проверка безопасности» (БЕЗ.ФТ.4-6).
 *
 * Тонкость, ради которой экран сложнее списка: после автопересчёта БЕЗ.ФТ.3
 * отчёт относится **к другой версии плана**. Показываем баннер «План
 * пересчитан», переходим на новую версию `replace`, чтобы «назад» не вернуло
 * на устаревшую, и показываем счётчик попыток N из 3.
 */
import { useEffect, useState } from "react"
import { useNavigate, useParams } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api } from "@/api/client"
import { LegendSlot, Sidebar, useStep } from "@/app/AppShell"
import { EnvironmentLayers } from "@/map/EnvironmentLayers"
import { PlanRoutes, UavLegend } from "@/map/PlanRoutes"
import { useToast } from "@/shared/ui/Toasts"
import { JobProgress, isActive, useJob } from "@/features/planning/JobProgress"
import { ViolationMarkers } from "./ViolationMarkers"

const CHECK_ORDER = [
  "geozones",
  "airspace",
  "energy",
  "reachability",
  "coverage",
  "daylight",
  "separation",
] as const
const NO_HIDDEN: ReadonlySet<string> = new Set()
const MAX_AUTO_RECALC = 3

export function SafetyScreen() {
  const { taskId, planId } = useParams()
  const navigate = useNavigate()
  const toast = useToast()
  const queryClient = useQueryClient()
  const [jobId, setJobId] = useState<string | null>(null)
  const [recalculated, setRecalculated] = useState<number | null>(null)
  const [highlight, setHighlight] = useState<string | null>(null)

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
  const job = useJob(jobId)

  const goToCheckedPlan = (checkedPlanId: string, version?: number) => {
    if (checkedPlanId === planId) return
    if (version) setRecalculated(version)
    queryClient.invalidateQueries({ queryKey: ["plans", taskId] })
    navigate(`/tasks/${taskId}/plans/${checkedPlanId}/safety`, { replace: true })
  }

  const run = useMutation({
    mutationFn: () => api.runSafetyCheck(planId!),
    onSuccess: async ({ report: result, job: started }) => {
      if (started) {
        setJobId(started.id)
        return
      }
      if (!result) return
      queryClient.invalidateQueries({ queryKey: ["safety"] })
      queryClient.invalidateQueries({ queryKey: ["plan", result.plan_id] })
      if (result.plan_id !== planId) {
        const recalculatedPlan = await api.getPlan(result.plan_id)
        goToCheckedPlan(result.plan_id, recalculatedPlan.version)
      }
    },
    onError: (error: Error) => toast(error.message, true),
  })

  // Отчёт, найденный по этой версии плана, может относиться к другой версии:
  // после автопересчёта БЕЗ.ФТ.3 проверенной оказывается новая. Экран обязан
  // показывать ту версию, о которой отчёт, — иначе оператор читает результат
  // проверки под заголовком другого плана. Срабатывает и при обычном открытии
  // страницы, не только сразу после запуска проверки.
  const reportPlanId = report.data?.plan_id ?? null
  useEffect(() => {
    if (!reportPlanId || reportPlanId === planId) return
    api.getPlan(reportPlanId).then((p) => goToCheckedPlan(reportPlanId, p.version))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reportPlanId, planId])

  // Проверка ушла в очередь — дожидаемся её и переходим на проверенную версию.
  const finishedReportPlan = job.data?.status === "Завершен" ? job.data.result_plan_id : null
  useEffect(() => {
    if (!finishedReportPlan) return
    setJobId(null)
    queryClient.invalidateQueries({ queryKey: ["safety"] })
    if (finishedReportPlan !== planId) {
      api.getPlan(finishedReportPlan).then((p) => goToCheckedPlan(finishedReportPlan, p.version))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [finishedReportPlan])

  // Подтверждённый план уже зафиксирован: менять отметки после подтверждения
  // бессмысленно, оно от них не откатывается.
  const planLocked =
    plan.data?.status === "Подтвержден" || plan.data?.status === "Выгружен"

  // Оператор принимает риск конкретного нарушения. Если приняты все — план
  // можно подтвердить вопреки ЭКС.ФТ.2. Сводная строка «...и ещё N» тоже
  // отдельная запись: принимая её, оператор принимает и невидимые нарушения,
  // поэтому она показывается как обычная строка со своей галочкой.
  const ignore = useMutation({
    mutationFn: ({ violationId, ignored }: { violationId: string; ignored: boolean }) =>
      api.ignoreViolation(current!.id, violationId, ignored),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["safety"] })
    },
    onError: (err: Error) => toast(err.message || "Не удалось отметить нарушение"),
  })

  const recheck = useMutation({
    mutationFn: () => api.recheckSafety(planId!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["safety", planId] })
      toast("Проверка выполнена повторно")
    },
    onError: (error: Error) => toast(error.message, true),
  })

  const detail = plan.data ?? null
  const current = report.data ?? null
  const passed = current?.status === "Пройдена"
  const checks = current
    ? CHECK_ORDER.map((name) => current.checks.find((c) => c.name === name)).filter(Boolean)
    : []
  const uavIds = detail ? [...new Set(detail.sorties.map((s) => s.uav_id))] : []

  const sidebar = (
    <>
      <a
        href="#"
        className="back-link"
        onClick={(event) => {
          event.preventDefault()
          navigate(`/tasks/${taskId}/plans/${planId}`)
        }}
      >
        ← К плану
      </a>
      <p className="sb-title">Проверка безопасности</p>
      <p className="sb-sub">
        {task.data?.name ?? "…"}
        {detail ? ` · план версии ${detail.version}` : ""}
      </p>

      {recalculated ? (
        <div className="warning-box">
          План пересчитан автоматически, показана версия {recalculated}.
        </div>
      ) : null}

      {job.data && isActive(job.data) ? (
        <JobProgress job={job.data} cancelling={false} onCancel={() => {}} />
      ) : null}

      {!current ? (
        <>
          <p className="sb-sub">
            Проверка по этой версии плана ещё не выполнялась. Проверяются все семь критериев
            Таблицы 1; при нарушении система выполнит до трёх автоматических пересчётов.
          </p>
          <button
            className="btn primary"
            style={{ width: "100%" }}
            disabled={run.isPending || Boolean(jobId)}
            onClick={() => run.mutate()}
          >
            {run.isPending || jobId ? "Проверка…" : "Проверить безопасность"}
          </button>
        </>
      ) : (
        <>
          <span className={passed ? "status-badge ok" : "status-badge bad"}>{current.status}</span>
          {current.auto_recalc_count > 0 ? (
            <p className="hint" style={{ margin: "0 0 14px" }}>
              Автоматических пересчетов по этой задаче: {current.auto_recalc_count} из{" "}
              {MAX_AUTO_RECALC}.
            </p>
          ) : null}

          <ul className="safety-checks">
            {checks.map((check) => (
              <li key={check!.name} className={check!.passed ? "safety-check" : "safety-check bad"}>
                <div className="sc-row">
                  <span className="sc-dot" />
                  <span className="sc-label">{check!.label}</span>
                  <span className="sc-result">{check!.passed ? "пройдена" : "нарушение"}</span>
                </div>
                {!check!.passed && check!.violations.length ? (
                  <ul className="sc-violations">
                    {check!.violations.map((violation) => (
                      <li key={violation.id}>
                        <label className="violation">
                          <input
                            type="checkbox"
                            checked={violation.ignored}
                            disabled={ignore.isPending || planLocked}
                            onChange={(event) =>
                              ignore.mutate({
                                violationId: violation.id,
                                ignored: event.target.checked,
                              })
                            }
                          />
                          <span>{violation.message}</span>
                        </label>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </li>
            ))}
          </ul>

          {!passed ? (
            <div className="detail-actions" style={{ marginTop: 14 }}>
              <button className="btn" disabled={recheck.isPending} onClick={() => recheck.mutate()}>
                Повторить проверку
              </button>
              <button className="btn primary" onClick={() => navigate(`/tasks/${taskId}`)}>
                Вернуться к задаче
              </button>
            </div>
          ) : (
            <button
              className="btn primary"
              style={{ width: "100%", marginTop: 14 }}
              onClick={() => navigate(`/tasks/${taskId}/plans/${planId}/export`)}
            >
              Далее: подтверждение и экспорт
            </button>
          )}
        </>
      )}
    </>
  )

  useStep(3, [0, 1, 2], (index) => {
        if (index === 0 && task.data) navigate(`/environments/${task.data.environment_id}`)
        if (index === 1) navigate(`/tasks/${taskId}`)
        if (index === 2) navigate(`/tasks/${taskId}/plans/${planId}`)
      })

  return (
    <>
      <EnvironmentLayers environment={environment.data ?? null} hidden={NO_HIDDEN} dimmed />
      <PlanRoutes plan={detail} area={task.data?.area ?? null} highlightUav={highlight} />
      <ViolationMarkers report={current} />
      <Sidebar>{sidebar}</Sidebar>
      <LegendSlot>
        <UavLegend uavIds={uavIds} onHover={setHighlight} />
      </LegendSlot>
    </>
  )
}
