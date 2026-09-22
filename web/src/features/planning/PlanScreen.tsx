/**
 * Экран 4 «Планирование» (ПЛН.ФТ.5-10).
 *
 * Экран обслуживает два состояния сразу: идущий фоновый расчёт и готовый
 * план. Работа берётся не только из ответа на «Рассчитать», но и из
 * `GET /api/plan-jobs?task_id=` — иначе перезагрузка вкладки посреди
 * получасового расчёта теряла бы индикатор.
 */
import { useEffect, useState } from "react"
import { useNavigate, useParams } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api } from "@/api/client"
import { LegendSlot, Sidebar, useStep } from "@/app/AppShell"
import { EnvironmentLayers } from "@/map/EnvironmentLayers"
import { PlanRoutes, UavLegend } from "@/map/PlanRoutes"
import { formatDuration, formatUtc } from "@/shared/format"
import { useToast } from "@/shared/ui/Toasts"
import { JobProgress, isActive, useActiveJob } from "./JobProgress"

const NO_HIDDEN: ReadonlySet<string> = new Set()

export function PlanScreen() {
  const { taskId, planId } = useParams()
  const navigate = useNavigate()
  const toast = useToast()
  const queryClient = useQueryClient()
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
  const versions = useQuery({
    queryKey: ["plans", taskId],
    queryFn: () => api.listPlans(taskId!),
    enabled: Boolean(taskId),
  })
  const plan = useQuery({
    queryKey: ["plan", planId],
    queryFn: () => api.getPlan(planId!),
    enabled: Boolean(planId),
  })
  const job = useActiveJob(taskId ?? null)

  // Работа завершилась — забираем готовый план и переходим на него.
  const finishedPlanId = job.data?.status === "Завершен" ? job.data.result_plan_id : null
  useEffect(() => {
    if (!finishedPlanId || planId === finishedPlanId) return
    queryClient.invalidateQueries({ queryKey: ["plans", taskId] })
    queryClient.invalidateQueries({ queryKey: ["task", taskId] })
    navigate(`/tasks/${taskId}/plans/${finishedPlanId}`, { replace: true })
  }, [finishedPlanId, planId, taskId, navigate, queryClient])

  const recalculate = useMutation({
    mutationFn: () => api.createPlan(taskId!),
    onSuccess: ({ plan: created }) => {
      queryClient.invalidateQueries({ queryKey: ["plan-jobs", taskId] })
      queryClient.invalidateQueries({ queryKey: ["plans", taskId] })
      if (created) navigate(`/tasks/${taskId}/plans/${created.id}`)
    },
    onError: (error: Error) => toast(error.message, true),
  })

  const cancel = useMutation({
    mutationFn: (id: string) => api.cancelJob(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["plan-jobs", taskId] })
      toast("Расчёт отменён")
    },
    onError: (error: Error) => toast(error.message, true),
  })

  const detail = plan.data ?? null
  const running = isActive(job.data)
  const warnings = detail?.warnings ?? []
  const uavIds = detail ? [...new Set(detail.sorties.map((s) => s.uav_id))] : []
  const [expanded, setExpanded] = useState<string | null>(null)

  const sidebar = (
    <>
      <a
        href="#"
        className="back-link"
        onClick={(event) => {
          event.preventDefault()
          navigate(`/tasks/${taskId}`)
        }}
      >
        ← К задаче
      </a>
      <p className="sb-title">Результат расчета</p>
      <p className="sb-sub">
        {task.data?.name ?? "…"}
        {detail ? ` · версия ${detail.version}` : ""}
      </p>

      {job.data && (running || !detail) ? (
        <JobProgress
          job={job.data}
          cancelling={cancel.isPending}
          onCancel={() => cancel.mutate(job.data!.id)}
        />
      ) : null}

      {detail ? (
        <>
          <span className={warnings.length ? "status-badge bad" : "status-badge ok"}>
            {warnings.length ? "Есть предупреждения" : "Рассчитан"}
          </span>
          {warnings.length ? (
            <ul className="errors-list">
              {warnings.map((warning, index) => (
                <li key={index}>{warning}</li>
              ))}
            </ul>
          ) : null}

          <dl className="counts">
            <dt>Модель БВС</dt>
            <dd>{detail.uav_model}</dd>
            <dt>Критерий</dt>
            <dd>
              {detail.criterion_mode}
              {detail.criterion_mode === "Компромисс" ? ` (α=${detail.criterion_alpha})` : ""}
            </dd>
            <dt>J1 · общее время</dt>
            <dd>{formatDuration(detail.j1_s)}</dd>
            <dt>J2 · суммарный налет</dt>
            <dd>{formatDuration(detail.j2_s)}</dd>
            <dt>Вылетов</dt>
            <dd>{detail.sortie_count}</dd>
            <dt>Оптимальность</dt>
            <dd>{detail.is_optimal ? "оптимум решателя" : "эвристика, не гарантирована"}</dd>
          </dl>
          <p className="hint" style={{ margin: "0 0 14px" }}>
            Критерий меняется в модуле «Задача» — новый расчет создаст отдельную версию плана.
          </p>

          <h3 className="sb-section">Расписание вылетов</h3>
          {detail.sorties.length ? (
            <div className="schedule-wrap">
              <table className="schedule">
                <thead>
                  <tr>
                    <th>БВС</th>
                    <th>Старт</th>
                    <th>Финиш</th>
                    <th>Налет</th>
                  </tr>
                </thead>
                <tbody>
                  {[...detail.sorties]
                    .sort((a, b) => a.start_utc.localeCompare(b.start_utc))
                    .flatMap((sortie) => {
                      const key = `${sortie.uav_id}-${sortie.sortie_index}`
                      const open = expanded === key
                      const rows = [
                        <tr
                          key={key}
                          className={sortie.phases.length ? "expandable" : undefined}
                          onMouseEnter={() => setHighlight(sortie.uav_id)}
                          onMouseLeave={() => setHighlight(null)}
                          onClick={() =>
                            sortie.phases.length ? setExpanded(open ? null : key) : undefined
                          }
                        >
                          <td>
                            {sortie.phases.length ? (open ? "▾ " : "▸ ") : ""}
                            {sortie.uav_id} #{sortie.sortie_index + 1}
                          </td>
                          <td>{formatUtc(sortie.start_utc)}</td>
                          <td>{formatUtc(sortie.end_utc)}</td>
                          <td>{formatDuration(sortie.flight_time_s)}</td>
                        </tr>,
                      ]
                      // ИНТ.ФТ.15: этапы вылета по клику. Переходы строятся в
                      // обход зон, поэтому «взлёт — галсы — возврат» больше не
                      // выводится из одной строки расписания.
                      if (open) {
                        for (const [index, phase] of sortie.phases.entries()) {
                          rows.push(
                            <tr key={`${key}-p${index}`} className="phase">
                              <td colSpan={2}>
                                {phase.kind === "survey" ? "▪ " : "→ "}
                                {phase.label}
                              </td>
                              <td>{formatUtc(phase.end_utc)}</td>
                              <td>{Math.round(phase.distance_m)} м</td>
                            </tr>,
                          )
                        }
                      }
                      return rows
                    })}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="sb-sub">Ни одного вылета не назначено.</p>
          )}

          {(versions.data?.length ?? 0) > 1 ? (
            <div className="history">
              <h3>Версии плана</h3>
              <ul>
                {versions.data!.map((version) => (
                  <li key={version.id}>
                    <a
                      href={`/tasks/${taskId}/plans/${version.id}`}
                      onClick={(event) => {
                        event.preventDefault()
                        navigate(`/tasks/${taskId}/plans/${version.id}`)
                      }}
                    >
                      Версия {version.version} — J1 {formatDuration(version.j1_s)} / J2{" "}
                      {formatDuration(version.j2_s)}
                      {version.id === detail.id ? " (текущая)" : ""}
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          <button
            className="btn primary"
            style={{ width: "100%", marginTop: 14 }}
            onClick={() => navigate(`/tasks/${taskId}/plans/${detail.id}/safety`)}
          >
            Проверить безопасность
          </button>
        </>
      ) : null}

      <button
        className="btn"
        style={{ width: "100%", marginTop: 8 }}
        disabled={running || recalculate.isPending}
        onClick={() => recalculate.mutate()}
      >
        Пересчитать
      </button>
    </>
  )

  useStep(2, [0, 1], (index) => {
        if (index === 0 && task.data) navigate(`/environments/${task.data.environment_id}`)
        if (index === 1) navigate(`/tasks/${taskId}`)
      })

  return (
    <>
      <EnvironmentLayers environment={environment.data ?? null} hidden={NO_HIDDEN} dimmed />
      <PlanRoutes plan={detail} area={task.data?.area ?? null} highlightUav={highlight} />
      <Sidebar>{sidebar}</Sidebar>
      <LegendSlot>
        <UavLegend uavIds={uavIds} onHover={setHighlight} />
      </LegendSlot>
    </>
  )
}
