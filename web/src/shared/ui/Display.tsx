/** Отображение данных в панели: пары, плитки метрик, баннеры, вкладки, прогресс. */
import type { ReactNode } from "react"
import { Icon } from "./Icon"
import type { IconName } from "./Icon"

export function KeyValue({ rows }: { rows: ReadonlyArray<ReadonlyArray<ReactNode> | null | false> }) {
  return (
    <dl className="kv">
      {rows.filter(Boolean).map((row, index) => {
        const [key, value] = row as ReadonlyArray<ReactNode>
        return (
          <div key={index} style={{ display: "contents" }}>
            <dt>{key}</dt>
            <dd>{value}</dd>
          </div>
        )
      })}
    </dl>
  )
}

export function Tile({ label, value, sub }: { label: ReactNode; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="tile">
      <span className="cap" style={{ fontSize: 10 }}>
        {label}
      </span>
      <span className="v">{value}</span>
      {sub ? <span className="s">{sub}</span> : null}
    </div>
  )
}

export type BannerKind = "info" | "warn" | "bad" | "ok"
const BANNER_ICON: Record<BannerKind, IconName> = { info: "info", warn: "warn", bad: "error", ok: "check" }

export function Banner({ kind, children, role }: { kind: BannerKind; children: ReactNode; role?: "status" | "alert" }) {
  return (
    <div className={`banner ${kind}`} role={role}>
      <Icon name={BANNER_ICON[kind]} />
      <div>{children}</div>
    </div>
  )
}

export function Tabs<T extends string>({
  value,
  tabs,
  onChange,
  label,
}: {
  value: T
  tabs: ReadonlyArray<{ value: T; label: ReactNode; count?: number | null }>
  onChange: (value: T) => void
  label: string
}) {
  return (
    <div className="tabs" role="tablist" aria-label={label}>
      {tabs.map((tab) => (
        <button
          key={tab.value}
          type="button"
          role="tab"
          className="tab"
          aria-selected={tab.value === value}
          onClick={() => onChange(tab.value)}
        >
          {tab.label}
          {tab.count != null ? <span className="cnt">{tab.count}</span> : null}
        </button>
      ))}
    </div>
  )
}

export function ProgressBar({ value, running = false, label }: { value: number; running?: boolean; label: string }) {
  const clamped = Math.max(0, Math.min(100, Math.round(value)))
  return (
    <div
      className={running ? "progress running" : "progress"}
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={clamped}
    >
      <i style={{ width: `${clamped}%` }} />
    </div>
  )
}

export function SideHead({
  title,
  badge,
  sub,
  back,
}: {
  title: ReactNode
  badge?: ReactNode
  sub?: ReactNode
  back?: ReactNode
}) {
  return (
    <>
      {back}
      <div className="side-head">
        <div className="row">
          <h1 className="side-title">{title}</h1>
          {badge}
        </div>
        {sub ? <div className="muted sm">{sub}</div> : null}
      </div>
    </>
  )
}
