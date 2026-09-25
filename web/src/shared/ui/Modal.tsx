/**
 * Модальное окно: Esc и клик по фону закрывают, фокус удерживается внутри,
 * после закрытия возвращается на элемент, который окно открыл.
 */
import { useEffect, useId, useRef } from "react"
import type { ReactNode } from "react"
import { createPortal } from "react-dom"
import { IconButton } from "./Button"

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

export function Modal({
  title,
  onClose,
  children,
  footer,
  width,
}: {
  title: string
  onClose: () => void
  children: ReactNode
  footer?: ReactNode
  width?: number
}) {
  const dialog = useRef<HTMLDivElement>(null)
  const titleId = useId()
  const close = useRef(onClose)
  close.current = onClose

  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null
    const node = dialog.current
    const first = node?.querySelector<HTMLElement>("input, select, textarea") ?? node?.querySelector<HTMLElement>(FOCUSABLE)
    first?.focus()

    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation()
        close.current()
        return
      }
      if (event.key !== "Tab" || !node) return
      const items = [...node.querySelectorAll<HTMLElement>(FOCUSABLE)]
      if (!items.length) return
      const head = items[0]!
      const tail = items[items.length - 1]!
      if (event.shiftKey && document.activeElement === head) {
        event.preventDefault()
        tail.focus()
      } else if (!event.shiftKey && document.activeElement === tail) {
        event.preventDefault()
        head.focus()
      }
    }
    document.addEventListener("keydown", onKey)
    return () => {
      document.removeEventListener("keydown", onKey)
      opener?.focus?.()
    }
  }, [])

  return createPortal(
    <div className="backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div
        ref={dialog}
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        style={width ? { width } : undefined}
      >
        <div className="modal-h">
          <h2 id={titleId}>{title}</h2>
          <IconButton icon="close" label="Закрыть" onClick={onClose} />
        </div>
        <div className="modal-b">{children}</div>
        {footer ? <div className="modal-f">{footer}</div> : null}
      </div>
    </div>,
    document.body,
  )
}
