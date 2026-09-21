/** Тосты ИНТ.ФТ.19: автоскрытие через 5 с, закрытие по клику — как в legacy. */
import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react"
import type { ReactNode } from "react"

type Toast = { id: number; message: string; error: boolean }
type Notify = (message: string, error?: boolean) => void

const ToastContext = createContext<Notify>(() => {})

export const useToast = () => useContext(ToastContext)

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])
  const nextId = useRef(1)

  const dismiss = useCallback((id: number) => {
    setToasts((current) => current.filter((t) => t.id !== id))
  }, [])

  const notify = useCallback<Notify>(
    (message, error = false) => {
      const id = nextId.current++
      setToasts((current) => [...current, { id, message, error }])
      window.setTimeout(() => dismiss(id), 5000)
    },
    [dismiss],
  )

  const value = useMemo(() => notify, [notify])

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="toast-container">
        {toasts.map((toast) => (
          <div
            key={toast.id}
            className={toast.error ? "toast err" : "toast"}
            onClick={() => dismiss(toast.id)}
          >
            {toast.message}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}
