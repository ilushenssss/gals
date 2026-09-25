/**
 * Легенда карты: слои обстановки (ИНТ.ФТ.6) и борта плана (ИНТ.ФТ.13).
 * Чекбокс временно скрывает слой или маршрут борта, данные не меняются.
 */
import { useState } from "react"
import type { ReactNode } from "react"
import { LEGEND_ITEMS, cssColorForUav } from "./style"
import type { LayerName } from "./style"
import { Icon } from "@/shared/ui/Icon"

const SWATCH: Record<LayerName | "area", ReactNode> = {
  launch_site: (
    <svg className="lg-sw" viewBox="0 0 22 14" aria-hidden="true">
      <g transform="translate(11 7) rotate(-20)">
        <rect x="-9" y="-3" width="18" height="6" rx="1" style={{ fill: "var(--map-site)" }} />
      </g>
    </svg>
  ),
  reserve_site: (
    <svg className="lg-sw" viewBox="0 0 22 14" aria-hidden="true">
      <circle cx="11" cy="7" r="5.5" style={{ fill: "var(--map-bg)", stroke: "var(--map-site)" }} strokeWidth="1.5" />
      <path d="M8,4 L14,10 M14,4 L8,10" style={{ stroke: "var(--map-site)" }} strokeWidth="1.5" />
    </svg>
  ),
  airspace: (
    <svg className="lg-sw" viewBox="0 0 22 14" aria-hidden="true">
      <rect x="1" y="1" width="20" height="12" rx="2" style={{ fill: "var(--map-airspace)", stroke: "var(--map-airspace)" }} fillOpacity="0.14" strokeWidth="1.5" />
    </svg>
  ),
  no_fly: (
    <svg className="lg-sw" viewBox="0 0 22 14" aria-hidden="true">
      <rect x="1" y="1" width="20" height="12" rx="2" fill="none" style={{ stroke: "var(--map-nofly)" }} strokeDasharray="3 2" />
      <rect x="4" y="3.5" width="14" height="7" fill="url(#gals-hatch-nofly)" style={{ stroke: "var(--map-nofly)" }} strokeWidth="1.5" />
    </svg>
  ),
  obstacle: (
    <svg className="lg-sw" viewBox="0 0 22 14" aria-hidden="true">
      <rect x="3" y="2" width="16" height="10" fill="url(#gals-dots-obstacle)" style={{ stroke: "var(--map-obstacle)" }} strokeDasharray="2 2" strokeWidth="1.5" />
    </svg>
  ),
  area: (
    <svg className="lg-sw" viewBox="0 0 22 14" aria-hidden="true">
      <rect x="1" y="1" width="20" height="12" rx="2" style={{ fill: "var(--map-area)", stroke: "var(--map-area)" }} fillOpacity="0.2" strokeWidth="2" />
    </svg>
  ),
}

export function EnvironmentLegend({
  counts,
  hidden,
  onToggle,
  showArea = false,
  collapsible = false,
}: {
  counts: Record<string, number>
  hidden: ReadonlySet<string>
  onToggle: (layer: string) => void
  showArea?: boolean
  collapsible?: boolean
}) {
  const [open, setOpen] = useState(!collapsible)
  const items = LEGEND_ITEMS.filter((item) => (counts[item.layer] ?? 0) > 0)
  if (!items.length && !showArea) return null
  return (
    <>
      {collapsible ? (
        <button type="button" className="lrow" aria-expanded={open} onClick={() => setOpen(!open)} style={{ color: "var(--text-muted)" }}>
          <Icon name={open ? "down" : "next"} size={12} />
          Слои обстановки ({items.length})
        </button>
      ) : (
        <div className="cap">Обстановка</div>
      )}
      {open ? (
        <>
          {items.map((item) => (
            <label key={item.layer} className="lrow">
              <input type="checkbox" checked={!hidden.has(item.layer)} onChange={() => onToggle(item.layer)} />
              {SWATCH[item.layer]}
              {item.label}
              <span className="c">{counts[item.layer]}</span>
            </label>
          ))}
          {showArea ? (
            <label className="lrow">
              <input type="checkbox" checked={!hidden.has("area")} onChange={() => onToggle("area")} />
              {SWATCH.area}
              Область облета
            </label>
          ) : null}
        </>
      ) : null}
    </>
  )
}

export function UavLegend({
  uavIds,
  sortiesByUav,
  hidden,
  onToggle,
  onHover,
  highlight,
}: {
  uavIds: string[]
  sortiesByUav?: Record<string, number>
  hidden: ReadonlySet<string>
  onToggle: (uavId: string) => void
  onHover?: (uavId: string | null) => void
  highlight?: string | null
}) {
  if (!uavIds.length) return null
  return (
    <>
      <div className="cap">Борта</div>
      {uavIds.map((id) => (
        <label
          key={id}
          className={id === highlight ? "lrow hover" : "lrow"}
          onMouseEnter={() => onHover?.(id)}
          onMouseLeave={() => onHover?.(null)}
        >
          <input type="checkbox" checked={!hidden.has(id)} onChange={() => onToggle(id)} />
          <span className="sw" style={{ background: cssColorForUav(id, uavIds) }} />
          <span className="mono">{id}</span>
          {sortiesByUav?.[id] ? <span className="c">{plural(sortiesByUav[id]!)}</span> : null}
        </label>
      ))}
      <div className="key">
        <span>
          <svg width="22" height="6" aria-hidden="true">
            <line x1="0" y1="3" x2="22" y2="3" stroke="currentColor" strokeWidth="2" strokeDasharray="4 3" />
          </svg>
          перелет
        </span>
        <span>
          <svg width="22" height="6" aria-hidden="true">
            <line x1="0" y1="3" x2="22" y2="3" stroke="currentColor" strokeWidth="2" />
          </svg>
          галс
        </span>
      </div>
    </>
  )
}

function plural(n: number): string {
  const mod10 = n % 10
  const mod100 = n % 100
  if (mod10 === 1 && mod100 !== 11) return `${n} вылет`
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return `${n} вылета`
  return `${n} вылетов`
}

/** Контейнер легенды в слоте каркаса. */
export function LegendBox({ children }: { children: ReactNode }) {
  return <div className="legend">{children}</div>
}
