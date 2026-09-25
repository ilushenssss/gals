/**
 * Тосты ИНТ.ФТ.19: правый верхний угол, 5 секунд, закрываются крестиком.
 *
 * Тон — третий аргумент прежней сигнатуры: `notify(msg)` — успех,
 * `notify(msg, true)` — ошибка, `notify(msg, "warn")` — предупреждение
 * (например, про световой день: задача сохранена, но есть на что взглянуть —
 * красным это показывать неверно). Ошибки объявляются диктору как alert.
 */
import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react"
import type { ReactNode } from "react"
import { Icon } from "./Icon"

type Tone = "ok" | "err" | "warn"
type Toast = { id: number; message: string; tone: Tone }
type Notify = (message: string, tone?: boolean | "warn") => void

const ToastContext = createContext<Notify>(() => {})

export const useToast = () => useContext(ToastContext)

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])
  const nextId = useRef(1)

  const dismiss = useCallback((id: number) => {
    setToasts((current) => current.filter((t) => t.id !== id))
  }, [])

  const notify = useCallback<Notify>(
    (message, tone = false) => {
      const id = nextId.current++
      const resolved: Tone = tone === "warn" ? "warn" : tone ? "err" : "ok"
      setToasts((current) => [...current.slice(-3), { id, message, tone: resolved }])
      window.setTimeout(() => dismiss(id), 5000)
    },
    [dismiss],
  )

  const value = useMemo(() => notify, [notify])

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="toasts" aria-live="polite">
        {toasts.map((toast) => (
          <div
            key={toast.id}
            className={toast.tone === "ok" ? "toast" : `toast ${toast.tone}`}
            role={toast.tone === "err" ? "alert" : "status"}
          >
            <Icon name={toast.tone === "ok" ? "check" : toast.tone === "warn" ? "warn" : "error"} />
            <div>{toast.message}</div>
            <button className="x" type="button" aria-label="Закрыть уведомление" onClick={() => dismiss(toast.id)}>
              <Icon name="close" size={14} />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}
