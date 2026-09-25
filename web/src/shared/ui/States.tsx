/**
 * Состояния загрузки, пустоты и ошибки. Раньше экран при 404 или обрыве сети
 * оставался пустым — оператор не понимал, ждать ему или что-то сломалось.
 */
import { useEffect, useState } from "react"
import type { ReactNode } from "react"
import type { UseQueryResult } from "@tanstack/react-query"
import { ApiError } from "@/api/client"
import { Button } from "./Button"
import { Icon } from "./Icon"
import type { IconName } from "./Icon"

export function Skeleton({ lines = 4 }: { lines?: number }) {
  // Скелетон появляется только если ответ дольше 300 мс — иначе быстрый
  // запрос мигал бы серыми полосами.
  const [visible, setVisible] = useState(false)
  useEffect(() => {
    const timer = window.setTimeout(() => setVisible(true), 300)
    return () => window.clearTimeout(timer)
  }, [])
  if (!visible) return null
  return (
    <div className="section" aria-busy="true" aria-label="Загрузка">
      <div className="skel" style={{ width: "60%", height: 18 }} />
      <div className="skel" style={{ width: "40%" }} />
      {Array.from({ length: lines }, (_, index) => (
        <div key={index} className="skel" style={{ height: 40 }} />
      ))}
    </div>
  )
}

export function EmptyState({
  icon = "list",
  title,
  children,
  action,
  dashed = false,
}: {
  icon?: IconName
  title: ReactNode
  children?: ReactNode
  action?: ReactNode
  dashed?: boolean
}) {
  return (
    <div className={dashed ? "state dashed" : "state"}>
      <span className="muted">
        <Icon name={icon} size={26} />
      </span>
      <div className="title">{title}</div>
      {children ? <div className="muted sm">{children}</div> : null}
      {action}
    </div>
  )
}

function describe(error: unknown): string {
  // fetch без ответа сервера бросает TypeError («Failed to fetch») — это
  // обрыв связи, а не ошибка данных, и сказать надо именно это.
  if (!(error instanceof ApiError)) return "Сервер недоступен — проверьте соединение и повторите."
  return error.message
}

export function ErrorState({
  error,
  onRetry,
  action,
}: {
  error: unknown
  onRetry?: () => void
  action?: ReactNode
}) {
  const notFound = error instanceof ApiError && error.status === 404
  return (
    <div className="state" role="alert">
      <span style={{ color: "var(--danger)" }}>
        <Icon name="error" size={26} />
      </span>
      <div className="title">{notFound ? "Не найдено" : "Не удалось загрузить данные"}</div>
      <div className="muted sm">
        {notFound ? "Запись удалена или ссылка устарела." : describe(error)}
      </div>
      <div style={{ display: "flex", gap: 8 }}>
        {onRetry && !notFound ? (
          <Button size="sm" onClick={onRetry}>
            Повторить
          </Button>
        ) : null}
        {action}
      </div>
    </div>
  )
}

/**
 * Обертка над запросом: скелетон, пока грузится, ошибка с повтором, иначе
 * содержимое. `data` передается уже без `undefined`.
 */
export function QueryState<T>({
  query,
  children,
  skeleton,
  errorAction,
}: {
  query: UseQueryResult<T>
  children: (data: T) => ReactNode
  skeleton?: ReactNode
  errorAction?: ReactNode
}) {
  if (query.isPending) return <>{skeleton ?? <Skeleton />}</>
  if (query.isError) return <ErrorState error={query.error} onRetry={() => query.refetch()} action={errorAction} />
  return <>{children(query.data as T)}</>
}
