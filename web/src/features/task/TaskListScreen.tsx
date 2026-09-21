/** Список задач обстановки (ЗАД.ФТ.6-8). */
import { useNavigate, useParams } from "react-router-dom"
import { useQuery } from "@tanstack/react-query"
import { api } from "@/api/client"
import { Sidebar, useStep } from "@/app/AppShell"
import { EnvironmentLayers } from "@/map/EnvironmentLayers"

const NO_HIDDEN: ReadonlySet<string> = new Set()

export function TaskListScreen() {
  const { environmentId } = useParams()
  const navigate = useNavigate()

  const environment = useQuery({
    queryKey: ["environment", environmentId],
    queryFn: () => api.getEnvironment(environmentId!),
    enabled: Boolean(environmentId),
  })
  const tasks = useQuery({
    queryKey: ["tasks", environmentId],
    queryFn: () => api.listTasks(environmentId!),
    enabled: Boolean(environmentId),
  })

  const sidebar = (
    <>
      <a
        href="#"
        className="back-link"
        onClick={(event) => {
          event.preventDefault()
          navigate(`/environments/${environmentId}`)
        }}
      >
        ← Обстановка
      </a>
      <p className="sb-title">Задачи</p>
      <p className="sb-sub">Обстановка: {environment.data?.name ?? "…"}</p>
      <button
        className="btn primary"
        style={{ width: "100%", marginBottom: 14 }}
        onClick={() => navigate(`/environments/${environmentId}/tasks/new`)}
      >
        Создать задачу
      </button>
      {tasks.data?.length ? (
        <ul className="task-list">
          {tasks.data.map((task) => (
            <li key={task.id}>
              <a
                href={`/tasks/${task.id}`}
                onClick={(event) => {
                  event.preventDefault()
                  navigate(`/tasks/${task.id}`)
                }}
              >
                <span className="t-name">{task.name}</span>
                <span className="t-meta">
                  {task.survey_type} · {task.work_date} · {task.status}
                </span>
              </a>
            </li>
          ))}
        </ul>
      ) : (
        <p className="sb-sub">Пока нет ни одной задачи для этой обстановки.</p>
      )}
    </>
  )

  useStep(1, [0], (index) => index === 0 && navigate(`/environments/${environmentId}`))

  return (
    <>
      <EnvironmentLayers environment={environment.data ?? null} hidden={NO_HIDDEN} />
      <Sidebar>{sidebar}</Sidebar>
    </>
  )
}
