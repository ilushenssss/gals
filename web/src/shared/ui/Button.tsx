/**
 * Кнопка дизайн-системы. Primary — одно главное действие этапа, danger —
 * только отмена и сброс. Спиннер встроен и не меняет ширину подписи.
 */
import type { ButtonHTMLAttributes, ReactNode } from "react"
import { Icon } from "./Icon"
import type { IconName } from "./Icon"

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger"

type Props = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant
  size?: "sm" | "md" | "lg"
  block?: boolean
  busy?: boolean
  icon?: IconName
  iconAfter?: IconName
  children?: ReactNode
}

export function buttonClass({
  variant = "secondary",
  size = "md",
  block = false,
  extra,
}: { variant?: ButtonVariant; size?: "sm" | "md" | "lg"; block?: boolean; extra?: string }) {
  return [
    "btn",
    variant === "secondary" ? "" : variant,
    size === "md" ? "" : size,
    block ? "block" : "",
    extra ?? "",
  ]
    .filter(Boolean)
    .join(" ")
}

export function Button({
  variant,
  size,
  block,
  busy = false,
  icon,
  iconAfter,
  className,
  disabled,
  children,
  type = "button",
  ...rest
}: Props) {
  const iconSize = size === "sm" ? 14 : 15
  return (
    <button
      {...rest}
      type={type}
      className={buttonClass({ variant, size, block, extra: className })}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
    >
      {busy ? <span className="spin" /> : icon ? <Icon name={icon} size={iconSize} /> : null}
      {children}
      {iconAfter ? <Icon name={iconAfter} size={iconSize} /> : null}
    </button>
  )
}

export function IconButton({
  icon,
  label,
  className,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { icon: IconName; label: string }) {
  return (
    <button type="button" {...rest} className={["iconbtn", className].filter(Boolean).join(" ")} aria-label={label} title={label}>
      <Icon name={icon} size={15} />
    </button>
  )
}
