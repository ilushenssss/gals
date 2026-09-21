/** Легенда карты (ИНТ.ФТ.6): чекбоксы показа слоёв, только непустые слои. */
import type { EnvironmentDetail } from "@/api/client"
import { LEGEND_ITEMS } from "@/map/style"

export function Legend({
  environment,
  hidden,
  onToggle,
}: {
  environment: EnvironmentDetail | null
  hidden: ReadonlySet<string>
  onToggle: (layer: string) => void
}) {
  if (!environment) return null
  const layers = environment.layers as Record<string, unknown[]>
  const items = LEGEND_ITEMS.filter((item) => (layers[item.layer] ?? []).length > 0)
  if (!items.length) return null

  return (
    <div className="legend">
      {items.map((item) => (
        <label key={item.layer}>
          <input
            type="checkbox"
            checked={!hidden.has(item.layer)}
            onChange={() => onToggle(item.layer)}
          />
          <span className="sw" style={{ background: item.color }} />
          {item.label}
        </label>
      ))}
    </div>
  )
}
