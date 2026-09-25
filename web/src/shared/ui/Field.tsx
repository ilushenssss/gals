/**
 * Поле формы: подпись, контрол, подсказка и ошибка этого поля.
 *
 * Идентификатор генерируется, если не задан, и связывает подпись с контролом
 * (`htmlFor`), а ошибку — через `aria-describedby`: экранный диктор читает
 * причину отказа вместе с полем, а не где-то внизу формы.
 */
import { cloneElement, isValidElement, useId } from "react"
import type { ReactElement, ReactNode } from "react"

export function Field({
  label,
  hint,
  error,
  id,
  children,
  className,
}: {
  label?: ReactNode
  hint?: ReactNode
  error?: string | null
  id?: string
  children: ReactElement<Record<string, unknown>>
  className?: string
}) {
  const autoId = useId()
  const controlId = id ?? autoId
  const hintId = `${controlId}-hint`
  const errorId = `${controlId}-err`
  const describedBy = [hint ? hintId : null, error ? errorId : null].filter(Boolean).join(" ") || undefined
  const extra: Record<string, unknown> = { id: controlId }
  if (error) extra["aria-invalid"] = true
  if (describedBy) extra["aria-describedby"] = describedBy
  const control = isValidElement(children) ? cloneElement(children, extra) : children
  return (
    <div className={["field", className].filter(Boolean).join(" ")}>
      {label ? <label htmlFor={controlId}>{label}</label> : null}
      {control}
      {hint ? (
        <span className="hint" id={hintId}>
          {hint}
        </span>
      ) : null}
      {error ? (
        <span className="err" id={errorId} role="alert">
          {error}
        </span>
      ) : null}
    </div>
  )
}

/** Сегментированный выбор одного значения (критерий, способ задания области). */
export function Segmented<T extends string>({
  name,
  value,
  options,
  onChange,
  label,
  disabled,
}: {
  name: string
  value: T
  options: ReadonlyArray<{ value: T; label: ReactNode; disabled?: boolean }>
  onChange: (value: T) => void
  label: string
  disabled?: boolean
}) {
  return (
    <div className="seg" role="radiogroup" aria-label={label}>
      {options.map((option) => (
        <label key={option.value}>
          <input
            type="radio"
            name={name}
            value={option.value}
            checked={value === option.value}
            disabled={disabled || option.disabled}
            onChange={() => onChange(option.value)}
          />
          {option.label}
        </label>
      ))}
    </div>
  )
}
