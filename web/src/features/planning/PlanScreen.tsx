/**
 * Этап 3 «Планирование» (ПЛН.ФТ.5–10, ИНТ.ФТ.10–13).
 *
 * Экран обслуживает оба состояния: идущий фоновый расчет и готовый план.
 * Работа берется не только из ответа на «Рассчитать», но и из
 * `GET /api/plan-jobs?task_id=` — иначе перезагрузка вкладки посреди
 * получасового расчета теряла бы индикатор. Пока расчет идет, карта
 * показывает область без маршрутов (ИНТ.ФТ.10).
 *
 * Результат — три вкладки ИНТ.ФТ.12: расписание (строка вылета
 * раскрывается в этапы, наведение подсвечивает маршрут), метрики с
 * журналом расчета, версии плана.
 */
import { useEffect, useMemo, useRef, useState } from "react"
import { Link, useNavigate, useParams } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api, isJobOutcome } from "@/api/client"
import type { PlanDetail } from "@/api/client"
import { LegendSlot, Sidebar } from "@/app/AppShell"
import { EnvironmentLayers } from "@/map/EnvironmentLayers"
import { AreaLayer } from "@/map/AreaLayer"
import { PlanRoutes, planUavIds } from "@/map/PlanRoutes"
import { LegendBox, UavLegend } from "@/map/Legend"
import { cssColorForUav } from "@/map/style"
import { Button, buttonClass } from "@/shared/ui/Button"
import { Banner, KeyValue, SideHead, Tabs, Tile } from "@/shared/ui/Display"
import { Icon } from "@/shared/ui/Icon"
import { EmptyState, Skeleton } from "@/shared/ui/States"
import { StatusBadge } from "@/shared/ui/StatusBadge"
import { useToast } from "@/shared/ui/Toasts"
import { formatDateTime, formatDuration, formatNumber, formatTime, plural, zoneLabel } from "@/shared/format"
import { useToggleSet } from "@/shared/useToggleSet"
import { STEP, useTaskChrome, useTaskFlow } from "@/features/task/useTaskFlow"
import { JobOutcome, JobProgress, isActive, useActiveJob } from "./JobProgress"

const NO_HIDDEN: ReadonlySet<string> = new Set()
type Tab = "schedule" | "metrics" | "versions"

function Schedule({
  plan,
  timeZone,
  highlight,
  onHover,
}: {
  plan: PlanDetail
  timeZone: string | null | undefined
  highlight: string | null
  onHover: (uav: string | null) => void
}) {
  const [expanded, setExpanded] = useState<string | null>(null)
  const uavIds = planUavIds(plan)
  const sorties = useMemo(() => [...plan.sorties].sort((a, b) => a.start_utc.localeCompare(b.start_utc)), [plan])
  if (!sorties.length) return <EmptyState title="Ни одного вылета не назначено" />
  return (
    <>
      <div className="tbl-wrap">
        <table className="tbl">
          <thead>
            <tr>
              <th style={{ width: 16 }} aria-label="Раскрыть" />
              <th>Борт</th>
              <th>№</th>
              <th>Старт–финиш</th>
              <th className="r">Налет</th>
              <th className="r">Путь</th>
            </tr>
          </thead>
          <tbody>
            {sorties.flatMap((sortie) => {
              const key = `${sortie.uav_id}-${sortie.sortie_index}`
              const open = expanded === key
              const rows = [
                <tr
                  key={key}
                  className={sortie.uav_id === highlight ? "row hl" : "row"}
                  tabIndex={0}
                  aria-expanded={sortie.phases.length ? open : undefined}
                  onMouseEnter={() => onHover(sortie.uav_id)}
                  onMouseLeave={() => onHover(null)}
                  onFocus={() => onHover(sortie.uav_id)}
                  onBlur={() => onHover(null)}
                  onClick={() => sortie.phases.length && setExpanded(open ? null : key)}
                  onKeyDown={(event) => {
                    if ((event.key === "Enter" || event.key === " ") && sortie.phases.length) {
                      event.preventDefault()
                      setExpanded(open ? null : key)
                    }
                  }}
                >
                  <td className="muted">{sortie.phases.length ? <Icon name={open ? "down" : "next"} size={12} /> : null}</td>
                  <td>
                    <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                      <span className="sw" style={{ background: cssColorForUav(sortie.uav_id, uavIds) }} />
                      <span className="mono">{sortie.uav_id}</span>
                    </span>
                  </td>
                  <td className="mono">{sortie.sortie_index + 1}</td>
                  <td className="mono">
                    {formatTime(sortie.start_utc, timeZone)}–{formatTime(sortie.end_utc, timeZone)}
                  </td>
                  <td className="mono r">{formatDuration(sortie.flight_time_s)}</td>
                  <td className="mono r">{formatNumber(sortie.distance_m / 1000)}</td>
                </tr>,
              ]
              if (open) {
                sortie.phases.forEach((phase, index) =>
                  rows.push(
                    <tr key={`${key}-${index}`} className="phase">
                      <td />
                      <td colSpan={2}>
                        {phase.kind === "survey" ? "▪ " : "→ "}
                        {phase.label}
                        {phase.height_agl_m != null ? <span className="mono"> · {Math.round(phase.height_agl_m)} м AGL</span> : null}
                      </td>
                      <td className="mono">
                        {formatTime(phase.start_utc, timeZone)}–{formatTime(phase.end_utc, timeZone)}
                      </td>
                      <td className="mono r">{formatDuration((new Date(phase.end_utc).getTime() - new Date(phase.start_utc).getTime()) / 1000)}</td>
                      <td className="mono r">{formatNumber(phase.distance_m / 1000)}</td>
                    </tr>,
                  ),
                )
              }
              return rows
            })}
          </tbody>
        </table>
      </div>
      <p className="muted sm">
        Время — {zoneLabel(timeZone)}, путь в км. Наведение подсвечивает маршрут на карте, клик раскрывает этапы вылета с высотой над рельефом (AGL).
      </p>
    </>
  )
}

type SurveyGroup = {
  model: string
  camera: string
  height: number
  swath: number
  speed: number
  budget: number
  sorties: number
}

/**
 * Параметры съемки по группам «модель + камера». В смешанном парке у
 * вылетов разных моделей своя высота, полоса, скорость и бюджет; планы,
 * рассчитанные до появления этих полей у вылета, берут их из карточки.
 */
function surveyGroups(plan: PlanDetail): SurveyGroup[] {
  const groups = new Map<string, SurveyGroup>()
  for (const sortie of plan.sorties) {
    const model = sortie.model_key ?? plan.model_key
    const camera = sortie.camera_key ?? plan.camera_key
    const key = `${model}/${camera}`
    const group = groups.get(key)
    if (group) {
      group.sorties += 1
      continue
    }
    groups.set(key, {
      model,
      camera,
      height: sortie.height_m ?? plan.height_m,
      swath: sortie.swath_m ?? plan.swath_m,
      speed: sortie.cruise_speed_mps ?? plan.cruise_speed_mps,
      budget: sortie.budget_s ?? plan.budget_s,
      sorties: 1,
    })
  }
  return [...groups.values()]
}

function Metrics({ plan }: { plan: PlanDetail }) {
  const groups = surveyGroups(plan)
  const survey =
    groups.length > 1
      ? [
          ["Смешанный парк", plan.uav_model],
          ...groups.map((g) => [
            `${g.model} · ${g.camera}`,
            <span className="mono">
              {Math.round(g.height)} м · полоса {Math.round(g.swath)} м · {formatNumber(g.speed)} м/с ·{" "}
              {formatDuration(g.budget)} · {plural(g.sorties, "вылет", "вылета", "вылетов")}
            </span>,
          ]),
        ]
      : [
          ["Модель и камера", `${plan.uav_model} · ${plan.camera_key}`],
          ["Высота полета", <span className="mono">{Math.round(plan.height_m)} м</span>],
          ["Полоса захвата", <span className="mono">{Math.round(plan.swath_m)} м</span>],
          ["Крейсерская скорость", <span className="mono">{formatNumber(plan.cruise_speed_mps)} м/с</span>],
          ["Бюджет вылета", <span className="mono">{formatDuration(plan.budget_s)}</span>],
        ]
  return (
    <>
      <KeyValue
        rows={[
          ...survey,
          ["Критерий", plan.criterion_mode === "Компромисс" ? `Компромисс, α = ${formatNumber(plan.criterion_alpha, 2)}` : plan.criterion_mode],
          ["J1 · время до конца работ", <span className="mono">{formatDuration(plan.j1_s)}</span>],
          ["J2 · суммарный налет", <span className="mono">{formatDuration(plan.j2_s)}</span>],
          ["Оптимальность", plan.is_optimal ? "оптимум решателя" : "эвристика, не гарантирована"],
        ]}
      />
      <p className="muted sm">Критерий меняется в задаче — новый расчет создаст отдельную версию плана.</p>
      {plan.warnings.length ? (
        <div className="section">
          <div className="cap">Предупреждения · {plan.warnings.length}</div>
          {plan.warnings.map((warning, index) => (
            <Banner key={index} kind="warn">
              {warning}
            </Banner>
          ))}
        </div>
      ) : null}
      {plan.calculation_log.length ? (
        <details className="card" style={{ padding: "10px 12px" }}>
          <summary style={{ fontWeight: 600, fontSize: 13, cursor: "pointer" }}>История расчета</summary>
          <ol className="list" style={{ marginTop: 8, gap: 4 }}>
            {plan.calculation_log.map((line, index) => (
              <li key={index} className="sm">
                {line}
              </li>
            ))}
          </ol>
        </details>
      ) : null}
    </>
  )
}

export function PlanScreen() {
  const { taskId, planId } = useParams()
  const navigate = useNavigate()
  const toast = useToast()
  const queryClient = useQueryClient()
  const [highlight, setHighlight] = useState<string | null>(null)
  const [hiddenUavs, toggleUav] = useToggleSet()
  const [tab, setTab] = useState<Tab>("schedule")

  const flow = useTaskFlow(taskId, planId)
  useTaskChrome(flow, STEP.planning, taskId)
  const plan = useQuery({ queryKey: ["plan", planId], queryFn: () => api.getPlan(planId!), enabled: Boolean(planId) })
  const job = useActiveJob(taskId ?? null)

  // Открыт экран без версии — показываем последнюю, если расчет не идет.
  const running = isActive(job.data)
  useEffect(() => {
    if (!planId && !running && flow.latest && !job.isPending) navigate(`/tasks/${taskId}/plans/${flow.latest.id}`, { replace: true })
  }, [planId, running, flow.latest, job.isPending, taskId, navigate])

  // Работа завершилась — забираем готовый план и переходим на него. Только
  // если экран видел эту работу идущей: иначе при открытии любой версии
  // старая завершенная работа уводила бы на свой результат.
  const watched = useRef<string | null>(null)
  if (running && job.data) watched.current = job.data.id
  const finishedPlanId = job.data?.status === "Завершен" && job.data.id === watched.current ? job.data.result_plan_id : null
  useEffect(() => {
    if (!finishedPlanId) return
    watched.current = null
    queryClient.invalidateQueries({ queryKey: ["plans", taskId] })
    queryClient.invalidateQueries({ queryKey: ["task", taskId] })
    if (planId !== finishedPlanId) navigate(`/tasks/${taskId}/plans/${finishedPlanId}`, { replace: true })
  }, [finishedPlanId, planId, taskId, navigate, queryClient])

  const recalculate = useMutation({
    mutationFn: () => api.createPlan(taskId!),
    onSuccess: ({ plan: created }) => {
      queryClient.invalidateQueries({ queryKey: ["plan-jobs", taskId] })
      queryClient.invalidateQueries({ queryKey: ["plans", taskId] })
      queryClient.invalidateQueries({ queryKey: ["task", taskId] })
      if (created) navigate(`/tasks/${taskId}/plans/${created.id}`)
      else navigate(`/tasks/${taskId}/plans`)
    },
    onError: (error: Error) => {
      // Расчет закончился без плана еще в окне синхронного ожидания (409 —
      // лимит/отмена/сбой, 422 — невыполнимая задача): исход уже записан в
      // работе, его покажет карточка с действием, а не пятисекундный тост.
      if (isJobOutcome(error)) queryClient.invalidateQueries({ queryKey: ["plan-jobs", taskId] })
      else toast(error.message, true)
    },
  })

  const cancel = useMutation({
    mutationFn: (id: string) => api.cancelJob(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["plan-jobs", taskId] })
      toast("Отмена расчета запрошена")
    },
    onError: (error: Error) => toast(error.message, true),
  })

  const detail = plan.data ?? null
  const uavIds = useMemo(() => planUavIds(detail), [detail])
  const sortiesByUav = useMemo(
    () => (detail ? detail.sorties.reduce<Record<string, number>>((acc, s) => ({ ...acc, [s.uav_id]: (acc[s.uav_id] ?? 0) + 1 }), {}) : {}),
    [detail],
  )
  const versions = [...(flow.plans.data ?? [])].sort((a, b) => b.version - a.version)
  const failedJob = job.data && !running && job.data.status !== "Завершен" ? job.data : null
  // Невыполнимую задачу повторный запуск не исправит — ведем к ее правке.
  const outcomeAction =
    failedJob?.error_code === "infeasible" ? (
      <Link className={buttonClass({ size: "sm", variant: "primary" })} to={`/tasks/${taskId}/edit`}>
        Изменить задачу
      </Link>
    ) : (
      <Button size="sm" variant="primary" onClick={() => recalculate.mutate()}>
        Запустить снова
      </Button>
    )
  const failedAfterPlan = failedJob && detail && new Date(failedJob.queued_at) > new Date(detail.created_at)

  let body
  if (running && job.data) {
    body = (
      <>
        <SideHead title="Планирование" sub={`${flow.task.data?.name ?? "…"} · расчет новой версии`} />
        <JobProgress job={job.data} cancelling={cancel.isPending} onCancel={() => cancel.mutate(job.data!.id)} />
        <Banner kind="info">Вкладку можно закрыть — расчет идет на сервере. При возврате экран подключится к нему сам.</Banner>
      </>
    )
  } else if (planId && plan.isPending) {
    body = <Skeleton />
  } else if (detail) {
    body = (
      <>
        <SideHead
          title={`План · версия ${detail.version}`}
          badge={<StatusBadge status={detail.status} />}
          sub={`Рассчитан ${formatDateTime(detail.created_at)} · ${detail.uav_model}`}
        />
        {flow.planStale ? <Banner kind="warn">Задача изменена после этого расчета — план посчитан по прежним параметрам. Пересчитайте его.</Banner> : null}
        {failedAfterPlan && failedJob ? (
          <JobOutcome job={failedJob} actions={outcomeAction} />
        ) : null}
        <div className="tiles">
          <Tile label="J1 · время" value={formatDuration(detail.j1_s)} sub="до конца работ" />
          <Tile label="J2 · налет" value={formatDuration(detail.j2_s)} sub="сумма вылетов" />
          <Tile label="Вылеты" value={detail.sortie_count} sub={plural(uavIds.length, "борт", "борта", "бортов")} />
        </div>
        <Tabs<Tab>
          label="Результат планирования"
          value={tab}
          onChange={setTab}
          tabs={[
            { value: "schedule", label: "Расписание", count: detail.sorties.length },
            { value: "metrics", label: "Метрики", count: detail.warnings.length || null },
            { value: "versions", label: "Версии", count: versions.length },
          ]}
        />
        {tab === "schedule" ? <Schedule plan={detail} timeZone={flow.task.data?.timezone} highlight={highlight} onHover={setHighlight} /> : null}
        {tab === "metrics" ? <Metrics plan={detail} /> : null}
        {tab === "versions" ? (
          <>
            <ul className="list">
              {versions.map((version) => (
                <li key={version.id}>
                  <Link className="item" aria-current={version.id === detail.id} to={`/tasks/${taskId}/plans/${version.id}`}>
                    <span className="body-col">
                      <span className="t">
                        Версия {version.version}
                        {version.id === flow.latest?.id ? " · последняя" : ""}
                      </span>
                      <span className="m mono">
                        {formatDateTime(version.created_at)} · J1 {formatDuration(version.j1_s)} · J2 {formatDuration(version.j2_s)}
                      </span>
                    </span>
                    <StatusBadge status={version.status} />
                  </Link>
                </li>
              ))}
            </ul>
            <p className="muted sm">Каждый расчет создает новую версию. Проверка и подтверждение относятся к конкретной версии.</p>
          </>
        ) : null}
      </>
    )
  } else if (failedJob) {
    body = (
      <>
        <SideHead title="Планирование" sub={flow.task.data?.name} />
        <JobOutcome job={failedJob} actions={outcomeAction} />
      </>
    )
  } else if (job.isPending || flow.plans.isPending) {
    body = <Skeleton />
  } else {
    body = (
      <>
        <SideHead title="Планирование" sub={flow.task.data?.name} />
        <EmptyState icon="plane" title="План еще не рассчитан" action={<Button variant="primary" busy={recalculate.isPending} onClick={() => recalculate.mutate()}>Рассчитать</Button>}>
          Расчет подберет модель и камеру, построит галсы и расписание вылетов.
        </EmptyState>
      </>
    )
  }

  const footer = detail && !running ? (
    <>
      <Button busy={recalculate.isPending} onClick={() => recalculate.mutate()}>
        Пересчитать
      </Button>
      <Link className={buttonClass({ variant: "primary", extra: "push" })} to={`/tasks/${taskId}/plans/${detail.id}/safety`}>
        Проверить безопасность
      </Link>
    </>
  ) : null

  return (
    <>
      <EnvironmentLayers environment={flow.environment.data ?? null} hidden={NO_HIDDEN} dimmed />
      {running || !detail ? <AreaLayer area={flow.task.data?.area ?? null} boundsKey={flow.task.data ? `plan-area-${taskId}` : null} /> : null}
      {!running ? <PlanRoutes plan={detail} area={flow.task.data?.area ?? null} highlightUav={highlight} hiddenUavs={hiddenUavs} /> : null}
      <Sidebar footer={footer}>
        <Link className="back" to={`/tasks/${taskId}`}>
          <Icon name="back" size={14} />К задаче
        </Link>
        {body}
      </Sidebar>
      {detail && !running ? (
        <LegendSlot>
          <LegendBox>
            <UavLegend uavIds={uavIds} sortiesByUav={sortiesByUav} hidden={hiddenUavs} onToggle={toggleUav} onHover={setHighlight} highlight={highlight} />
          </LegendBox>
        </LegendSlot>
      ) : null}
    </>
  )
}
