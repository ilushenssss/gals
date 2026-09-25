/**
 * Каталог, вкладка «История» — список задач с поиском и фильтрами
 * (ЗАД.ФТ.6–7). Наведение на задачу показывает ее область на карте, клик
 * открывает карточку. Фильтр обстановки берется и из `?env=`: так на
 * историю ведет прежняя ссылка «задачи обстановки».
 */
import { useMemo, useState } from "react"
import { Link, useSearchParams } from "react-router-dom"
import { useQuery } from "@tanstack/react-query"
import { api } from "@/api/client"
import type { TaskSummary } from "@/api/client"
import { Sidebar } from "@/app/AppShell"
import { AreaLayer } from "@/map/AreaLayer"
import { Segmented } from "@/shared/ui/Field"
import { Icon } from "@/shared/ui/Icon"
import { EmptyState, ErrorState, Skeleton } from "@/shared/ui/States"
import { StatusBadge } from "@/shared/ui/StatusBadge"
import { thumbnailPath } from "@/shared/geo"
import { buttonClass } from "@/shared/ui/Button"
import { CatalogTabs } from "./CatalogTabs"

type StatusFilter = "all" | "Черновик" | "Рассчитана" | "Подтверждена"

function Thumb({ area }: { area: unknown }) {
  const d = useMemo(() => thumbnailPath(area, 56, 44), [area])
  return (
    <svg width="56" height="44" viewBox="0 0 56 44" aria-hidden="true" style={{ flex: "none", borderRadius: 6, background: "var(--map-bg)", border: "1px solid var(--border)" }}>
      <path d={d} style={{ fill: "var(--map-area)", stroke: "var(--map-area)" }} fillOpacity="0.2" strokeWidth="1.5" />
    </svg>
  )
}

export function HistoryScreen() {
  const [params, setParams] = useSearchParams()
  const [search, setSearch] = useState("")
  const [status, setStatus] = useState<StatusFilter>("all")
  const [hovered, setHovered] = useState<TaskSummary | null>(null)
  const envFilter = params.get("env") ?? ""

  const tasks = useQuery({ queryKey: ["tasks", "all"], queryFn: () => api.listTasks() })
  const environments = useQuery({ queryKey: ["environments"], queryFn: api.listEnvironments })

  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase()
    return [...(tasks.data ?? [])]
      .filter((task) => (status === "all" ? true : task.status === status))
      .filter((task) => (envFilter ? task.environment_id === envFilter : true))
      .filter((task) => (needle ? `${task.name} ${task.environment_name} ${task.fleet_name}`.toLowerCase().includes(needle) : true))
      .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
  }, [tasks.data, status, envFilter, search])

  let list
  if (tasks.isPending) list = <Skeleton />
  else if (tasks.isError) list = <ErrorState error={tasks.error} onRetry={() => tasks.refetch()} />
  else if (!tasks.data.length)
    list = (
      <EmptyState title="Задач пока нет" action={<Link className={buttonClass({ variant: "primary", size: "sm" })} to="/catalog/new">Создать задачу</Link>}>
        Первая задача создается на вкладке «Новая задача».
      </EmptyState>
    )
  else if (!filtered.length)
    list = (
      <EmptyState icon="search" title="Ничего не найдено">
        Измените поиск или фильтры.
      </EmptyState>
    )
  else
    list = (
      <>
        <div className="muted sm">
          {filtered.length} из {tasks.data.length} · сначала измененные недавно
        </div>
        <ul className="list">
          {filtered.map((task) => (
            <li key={task.id}>
              <Link
                className="item"
                to={`/tasks/${task.id}`}
                style={{ alignItems: "flex-start" }}
                onMouseEnter={() => setHovered(task)}
                onMouseLeave={() => setHovered(null)}
                onFocus={() => setHovered(task)}
                onBlur={() => setHovered(null)}
              >
                <Thumb area={task.area} />
                <span className="body-col" style={{ gap: 4 }}>
                  <span className="t">{task.name}</span>
                  <span className="m">
                    {task.environment_name} · {task.survey_type} · {new Date(task.work_date).toLocaleDateString("ru-RU")}
                  </span>
                  <span>
                    <StatusBadge status={task.status} />
                  </span>
                </span>
              </Link>
            </li>
          ))}
        </ul>
      </>
    )

  return (
    <>
      <AreaLayer area={hovered?.area ?? null} boundsKey={hovered?.id ?? null} />
      <Sidebar>
        <CatalogTabs />
        <div className="section">
          <div className="input-icon">
            <Icon name="search" />
            <input className="input" aria-label="Поиск задач" placeholder="Название, обстановка или парк" value={search} onChange={(event) => setSearch(event.target.value)} />
          </div>
          <Segmented<StatusFilter>
            name="task-status"
            label="Статус задачи"
            value={status}
            onChange={setStatus}
            options={[
              { value: "all", label: "Все" },
              { value: "Черновик", label: "Черновик" },
              { value: "Рассчитана", label: "Рассчитана" },
              { value: "Подтверждена", label: "Подтв." },
            ]}
          />
          <select
            className="select"
            aria-label="Обстановка"
            value={envFilter}
            onChange={(event) => {
              const next = new URLSearchParams(params)
              if (event.target.value) next.set("env", event.target.value)
              else next.delete("env")
              setParams(next, { replace: true })
            }}
          >
            <option value="">Все обстановки</option>
            {(environments.data ?? []).map((env) => (
              <option key={env.id} value={env.id}>
                {env.name}
              </option>
            ))}
          </select>
        </div>
        {list}
      </Sidebar>
    </>
  )
}
