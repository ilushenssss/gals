/** Карточка задачи (ЗАД.ФТ.11) плюс запуск расчёта (ПЛН.ФТ.5). */
import { useNavigate, useParams } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api } from "@/api/client"
import { Sidebar, useStep } from "@/app/AppShell"
import { EnvironmentLayers } from "@/map/EnvironmentLayers"
import { AreaLayer } from "@/map/AreaLayer"
import { useToast } from "@/shared/ui/Toasts"

const NO_HIDDEN: ReadonlySet<string> = new Set()

export function TaskDetailScreen() {
  const { taskId } = useParams()
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
  const plans = useQuery({
    queryKey: ["plans", taskId],
    queryFn: () => api.listPlans(taskId!),
    enabled: Boolean(taskId),
  })

  // Запуск расчёта: ответ двойной (200 план либо 202 работа). В обоих случаях
  // уходим на экран планирования — он умеет и показывать готовый план, и
  // вести индикатор прогресса.
  const calculate = useMutation({
    mutationFn: () => api.createPlan(taskId!),
    onSuccess: ({ plan, job }) => {
      queryClient.invalidateQueries({ queryKey: ["plans", taskId] })
      queryClient.invalidateQueries({ queryKey: ["plan-jobs", taskId] })
      queryClient.invalidateQueries({ queryKey: ["task", taskId] })
      if (plan) navigate(`/tasks/${taskId}/plans/${plan.id}`)
      else if (job) navigate(`/tasks/${taskId}/plans`)
    },
    onError: (error: Error) => toast(error.message, true),
  })

  const detail = task.data
  const editable = detail?.status !== "Подтверждена"

  const sidebar = detail ? (
    <>
      <a
        href="#"
        className="back-link"
        onClick={(event) => {
          event.preventDefault()
          navigate(`/environments/${detail.environment_id}/tasks`)
        }}
      >
        ← К списку задач
      </a>
      <p className="sb-title">{detail.name}</p>
      <p className="sb-sub">Обстановка: {detail.environment_name}</p>
      <span className="status-badge draft">{detail.status}</span>
      {detail.daylight_warning ? (
        <div className="warning-box">{detail.daylight_warning}</div>
      ) : null}
      <dl className="counts">
        <dt>Парк БВС</dt>
        <dd>{detail.fleet_name}</dd>
        <dt>Тип съемки</dt>
        <dd>{detail.survey_type}</dd>
        <dt>GSD</dt>
        <dd>{detail.gsd_cm} см</dd>
        <dt>Дата работ</dt>
        <dd>{detail.work_date}</dd>
        <dt>Окно работ</dt>
        <dd>
          {detail.window_start?.slice(0, 5) ?? "—"}–{detail.window_end?.slice(0, 5) ?? "—"}
        </dd>
        <dt>Ветер</dt>
        <dd>{detail.wind_speed_ms != null ? `${detail.wind_speed_ms} м/с` : "—"}</dd>
        <dt>Критерий</dt>
        <dd>
          {detail.criterion_mode}
          {detail.criterion_mode === "Компромисс" ? ` (α=${detail.criterion_alpha})` : ""}
        </dd>
      </dl>
      <div className="detail-actions">
        {editable ? (
          <button className="btn" onClick={() => navigate(`/tasks/${detail.id}/edit`)}>
            Редактировать
          </button>
        ) : null}
        <button
          className="btn primary"
          disabled={calculate.isPending}
          onClick={() => calculate.mutate()}
        >
          {calculate.isPending ? "Расчёт…" : "Рассчитать"}
        </button>
      </div>
      {plans.data?.length ? (
        <button
          className="btn"
          style={{ width: "100%", marginTop: 8 }}
          onClick={() => navigate(`/tasks/${detail.id}/plans/${plans.data[0]!.id}`)}
        >
          Посмотреть план
        </button>
      ) : null}
    </>
  ) : null

  useStep(1, [0], (index) => {
        if (index === 0 && detail) navigate(`/environments/${detail.environment_id}`)
        if (index === 1 && detail) navigate(`/environments/${detail.environment_id}/tasks`)
      })

  return (
    <>
      <EnvironmentLayers environment={environment.data ?? null} hidden={NO_HIDDEN} dimmed />
      <AreaLayer area={detail?.area ?? null} boundsKey={detail?.id ?? null} />
      <Sidebar>{sidebar}</Sidebar>
    </>
  )
}
