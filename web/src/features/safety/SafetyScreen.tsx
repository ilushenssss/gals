/**
 * Этап 4 «Проверка безопасности» (БЕЗ.ФТ.4–6, ИНТ.ФТ.14–15).
 *
 * Тонкость, ради которой экран сложнее списка: после автопересчета БЕЗ.ФТ.3
 * отчет относится **к другой версии плана**. Экран переходит на нее
 * (`replace`, чтобы «назад» не вернуло на устаревшую), показывает баннер
 * «План пересчитан» и счетчик попыток N из 3.
 *
 * Нарушение можно принять (риск под ответственность оператора) — по одному
 * или все сразу. Если приняты все, план подтверждается «вопреки
 * нарушениям», поэтому переход к экспорту открывается и в этом случае.
 */
import { useEffect, useMemo, useRef, useState } from "react"
import { Link, useNavigate, useParams } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api } from "@/api/client"
import type { SafetyCheckOut } from "@/api/client"
import { LegendSlot, Sidebar } from "@/app/AppShell"
import { EnvironmentLayers } from "@/map/EnvironmentLayers"
import { PlanRoutes, planUavIds } from "@/map/PlanRoutes"
import { LegendBox, UavLegend } from "@/map/Legend"
import { Button, buttonClass } from "@/shared/ui/Button"
import { Banner, SideHead } from "@/shared/ui/Display"
import { Icon } from "@/shared/ui/Icon"
import { EmptyState, Skeleton } from "@/shared/ui/States"
import { StatusBadge } from "@/shared/ui/StatusBadge"
import { useToast } from "@/shared/ui/Toasts"
import { formatDateTime } from "@/shared/format"
import { useToggleSet } from "@/shared/useToggleSet"
import { JobProgress, isActive, useActiveJob, useJob } from "@/features/planning/JobProgress"
import { STEP, useTaskChrome, useTaskFlow } from "@/features/task/useTaskFlow"
import { ViolationMarkers } from "./ViolationMarkers"

/** Порядок критериев — как в Таблице 1 модуля «Проверка безопасности». */
const CHECK_ORDER = ["geozones", "airspace", "altitude", "energy", "reachability", "coverage", "daylight", "separation"] as const
const NO_HIDDEN: ReadonlySet<string> = new Set()
const MAX_AUTO_RECALC = 3

function CheckCard({
  check,
  locked,
  busy,
  highlight,
  onHover,
  onToggle,
}: {
  check: SafetyCheckOut
  locked: boolean
  busy: boolean
  highlight: string | null
  onHover: (id: string | null) => void
  onToggle: (violationId: string, ignored: boolean) => void
}) {
  const [open, setOpen] = useState(!check.passed)
  const count = check.violations.length
  const accepted = check.violations.filter((v) => v.ignored).length
  return (
    <li className={check.passed ? "check ok" : "check bad"}>
      <button
        type="button"
        className="check-h"
        style={{ width: "100%", background: "none", border: 0, textAlign: "left", cursor: check.passed ? "default" : "pointer", color: "inherit" }}
        aria-expanded={check.passed ? undefined : open}
        onClick={() => !check.passed && setOpen(!open)}
      >
        <span className={check.passed ? "dot ok" : "dot bad"}>
          <Icon name={check.passed ? "check" : "close"} size={11} />
        </span>
        {check.label}
        <span className="res">
          {check.passed ? "пройдена" : accepted === count ? `принято ${accepted} из ${count}` : `нарушений: ${count}`}
        </span>
      </button>
      {!check.passed && open ? (
        <div className="check-b">
          {check.violations.map((violation) => (
            <label
              key={violation.id}
              className={["viol", violation.ignored ? "acc" : "", violation.id === highlight ? "hl" : ""].filter(Boolean).join(" ")}
              onMouseEnter={() => onHover(violation.id)}
              onMouseLeave={() => onHover(null)}
            >
              <input
                type="checkbox"
                checked={Boolean(violation.ignored)}
                disabled={busy || locked}
                aria-label={violation.ignored ? "Снять принятие риска" : "Принять риск по нарушению"}
                onChange={(event) => onToggle(violation.id, event.target.checked)}
              />
              <span className="txt">
                {violation.message}
                {violation.lat == null ? <span className="muted"> · без точки на карте</span> : null}
              </span>
            </label>
          ))}
          {check.recommendations.length ? (
            <div className="recs">
              <div className="cap" style={{ marginBottom: 4 }}>
                Как исправить
              </div>
              {check.recommendations.length === 1 ? (
                <div>{check.recommendations[0]}</div>
              ) : (
                <ul>
                  {check.recommendations.map((text, index) => (
                    <li key={index}>{text}</li>
                  ))}
                </ul>
              )}
            </div>
          ) : null}
        </div>
      ) : null}
    </li>
  )
}

export function SafetyScreen() {
  const { taskId, planId } = useParams()
  const navigate = useNavigate()
  const toast = useToast()
  const queryClient = useQueryClient()
  const [jobId, setJobId] = useState<string | null>(null)
  const [hiddenUavs, toggleUav] = useToggleSet()
  const [recalculated, setRecalculated] = useState<number | null>(null)
  const [highlightUav, setHighlightUav] = useState<string | null>(null)
  const [highlightViolation, setHighlightViolation] = useState<string | null>(null)

  const flow = useTaskFlow(taskId, planId)
  useTaskChrome(flow, STEP.safety, taskId)
  const plan = useQuery({ queryKey: ["plan", planId], queryFn: () => api.getPlan(planId!), enabled: Boolean(planId) })
  const report = flow.report
  const job = useJob(jobId)
  // Фоновая проверка с автопересчетом идет минутами и обязана пережить F5,
  // как и расчет плана: подхватываем идущую работу по этому плану.
  // handled — работы, исход которых уже показан: список работ обновляется
  // раз в 2 с и какое-то время еще числит завершенную активной.
  const handled = useRef(new Set<string>())
  const activeCheck = useActiveJob(taskId ?? null, "safety")
  const candidate = activeCheck.data
  const resumable = candidate && isActive(candidate) && candidate.plan_id === planId && !handled.current.has(candidate.id) ? candidate.id : null
  useEffect(() => {
    if (resumable && !jobId) setJobId(resumable)
  }, [resumable, jobId])

  const goToCheckedPlan = (checkedPlanId: string, version?: number) => {
    if (checkedPlanId === planId) return
    if (version) setRecalculated(version)
    queryClient.invalidateQueries({ queryKey: ["plans", taskId] })
    navigate(`/tasks/${taskId}/plans/${checkedPlanId}/safety`, { replace: true })
  }

  const run = useMutation({
    mutationFn: () => api.runSafetyCheck(planId!),
    onSuccess: async ({ report: result, job: started }) => {
      if (started) return setJobId(started.id)
      if (!result) return
      queryClient.invalidateQueries({ queryKey: ["safety"] })
      queryClient.invalidateQueries({ queryKey: ["plan", result.plan_id] })
      queryClient.invalidateQueries({ queryKey: ["plans", taskId] })
      if (result.plan_id !== planId) goToCheckedPlan(result.plan_id, (await api.getPlan(result.plan_id)).version)
    },
    onError: (error: Error) => toast(error.message, true),
  })

  // Отчет, найденный по этой версии, может относиться к другой: после
  // автопересчета проверенной оказывается новая. Экран обязан показывать ту
  // версию, о которой отчет, — и при обычном открытии страницы тоже.
  const reportPlanId = report.data?.plan_id ?? null
  useEffect(() => {
    if (!reportPlanId || reportPlanId === planId) return
    api.getPlan(reportPlanId).then((p) => goToCheckedPlan(reportPlanId, p.version))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reportPlanId, planId])

  // Проверка ушла в очередь — дожидаемся ее и переходим на проверенную версию.
  const finished = job.data && !isActive(job.data) ? job.data : null
  useEffect(() => {
    if (!finished) return
    handled.current.add(finished.id)
    setJobId(null)
    queryClient.invalidateQueries({ queryKey: ["plan-jobs", taskId] })
    queryClient.invalidateQueries({ queryKey: ["safety"] })
    queryClient.invalidateQueries({ queryKey: ["plans", taskId] })
    if (finished.status !== "Завершен") {
      toast(finished.error || `Проверка: ${finished.status.toLowerCase()}`, finished.status === "Отменен" ? "warn" : true)
      return
    }
    if (finished.result_plan_id && finished.result_plan_id !== planId) {
      api.getPlan(finished.result_plan_id).then((p) => goToCheckedPlan(finished.result_plan_id!, p.version))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [finished?.id])

  const cancel = useMutation({
    mutationFn: (id: string) => api.cancelJob(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["plan-job", jobId] }),
    onError: (error: Error) => toast(error.message, true),
  })

  const current = report.data && report.data.plan_id === planId ? report.data : null
  const ignore = useMutation({
    mutationFn: ({ violationId, ignored }: { violationId: string; ignored: boolean }) => api.ignoreViolation(current!.id, violationId, ignored),
    onSuccess: (updated) => queryClient.setQueryData(["safety", planId], updated),
    onError: (error: Error) => toast(error.message || "Не удалось отметить нарушение", true),
  })
  const ignoreAll = useMutation({
    mutationFn: (ignored: boolean) => api.ignoreAllViolations(current!.id, ignored),
    onSuccess: (updated) => queryClient.setQueryData(["safety", planId], updated),
    onError: (error: Error) => toast(error.message, true),
  })
  const recheck = useMutation({
    mutationFn: () => api.recheckSafety(planId!),
    onSuccess: (updated) => {
      queryClient.setQueryData(["safety", planId], updated)
      toast(updated.status === "Пройдена" ? "Проверка пройдена" : "Проверка выполнена повторно", updated.status === "Пройдена" ? false : "warn")
    },
    onError: (error: Error) => toast(error.message, true),
  })

  const detail = plan.data ?? null
  const planLocked = detail?.status === "Подтвержден" || detail?.status === "Выгружен"
  const passed = current?.status === "Пройдена"
  const checks = useMemo(
    () => (current ? CHECK_ORDER.map((name) => current.checks.find((c) => c.name === name)).filter((c): c is SafetyCheckOut => Boolean(c)) : []),
    [current],
  )
  const violations = checks.flatMap((c) => c.violations)
  const openViolations = violations.filter((v) => !v.ignored).length
  const uavIds = useMemo(() => planUavIds(detail), [detail])
  const checking = run.isPending || Boolean(jobId && isActive(job.data))

  const canExport = Boolean(current && (passed || current.violations_acknowledged))
  // В низу панели — только главное действие этапа; действия над отчетом
  // стоят рядом со списком критериев (см. reportActions).
  const footer =
    current && !checking ? (
      canExport ? (
        <Link className={buttonClass({ variant: "primary", block: true })} to={`/tasks/${taskId}/plans/${planId}/export`}>
          Далее: подтверждение
          <Icon name="next" size={15} />
        </Link>
      ) : (
        <Link className={buttonClass({ block: true })} to={`/tasks/${taskId}`}>
          Вернуться к задаче
        </Link>
      )
    ) : null
  const reportActions =
    current && !checking && !passed ? (
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <Button size="sm" busy={recheck.isPending} onClick={() => recheck.mutate()}>
          Повторить проверку
        </Button>
        {violations.length && !planLocked ? (
          <Button size="sm" variant="ghost" busy={ignoreAll.isPending} onClick={() => ignoreAll.mutate(openViolations > 0)}>
            {openViolations > 0 ? `Принять все (${openViolations})` : "Снять принятие"}
          </Button>
        ) : null}
      </div>
    ) : null

  let body
  if (checking && job.data) {
    body = <JobProgress job={job.data} title="Проверка безопасности" cancelLabel="Отменить проверку" cancelling={cancel.isPending} onCancel={() => cancel.mutate(job.data!.id)} />
  } else if (report.isPending || plan.isPending) {
    body = <Skeleton />
  } else if (!current) {
    body = (
      <EmptyState
        icon="shield"
        title="Проверка еще не выполнялась"
        action={
          <Button variant="primary" busy={checking} onClick={() => run.mutate()}>
            Проверить безопасность
          </Button>
        }
      >
        Проверяются восемь критериев. При нарушении система сама выполнит до {MAX_AUTO_RECALC} пересчетов плана.
      </EmptyState>
    )
  } else {
    body = (
      <>
        {current.auto_recalc_count > 0 ? (
          <p className="muted sm" style={{ marginTop: -8 }}>
            Автоматических пересчетов: <span className="mono">{current.auto_recalc_count} из {MAX_AUTO_RECALC}</span>
          </p>
        ) : null}
        {passed ? <Banner kind="ok">Все критерии пройдены — план можно подтверждать.</Banner> : null}
        {!passed && openViolations === 0 && violations.length ? (
          <Banner kind="warn">Все нарушения приняты оператором. План можно подтвердить с отметкой «вопреки нарушениям».</Banner>
        ) : null}
        {planLocked ? <Banner kind="info">План уже подтвержден — отметки о принятии риска больше не меняются.</Banner> : null}
        {reportActions}
        <ul className="checks">
          {checks.map((check) => (
            <CheckCard
              key={check.name}
              check={check}
              locked={planLocked}
              busy={ignore.isPending || ignoreAll.isPending}
              highlight={highlightViolation}
              onHover={setHighlightViolation}
              onToggle={(violationId, ignored) => ignore.mutate({ violationId, ignored })}
            />
          ))}
        </ul>
      </>
    )
  }

  return (
    <>
      <EnvironmentLayers environment={flow.environment.data ?? null} hidden={NO_HIDDEN} dimmed />
      <PlanRoutes plan={detail} area={flow.task.data?.area ?? null} highlightUav={highlightUav} hiddenUavs={hiddenUavs} />
      <ViolationMarkers report={current} highlight={highlightViolation} onHover={setHighlightViolation} />
      <Sidebar footer={footer}>
        <Link className="back" to={`/tasks/${taskId}/plans/${planId}`}>
          <Icon name="back" size={14} />К плану
        </Link>
        <SideHead
          title="Проверка безопасности"
          badge={current ? <StatusBadge status={current.status} /> : null}
          sub={detail ? `План версии ${detail.version}${current ? ` · проверен ${formatDateTime(current.created_at)}` : ""}` : "…"}
        />
        {recalculated ? (
          <Banner kind="info" role="status">
            План пересчитан автоматически — показана версия {recalculated}.
          </Banner>
        ) : null}
        {body}
      </Sidebar>
      <LegendSlot>
        <LegendBox>
          <UavLegend uavIds={uavIds} hidden={hiddenUavs} onToggle={toggleUav} onHover={setHighlightUav} highlight={highlightUav} />
        </LegendBox>
      </LegendSlot>
    </>
  )
}
