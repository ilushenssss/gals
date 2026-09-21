/** Стили слоёв обстановки — значения перенесены из legacy, не подобраны заново. */

export type LayerName = "airspace" | "no_fly" | "obstacle" | "launch_site" | "reserve_site"

export const LAYER_STYLE = {
  airspace: { cssColor: "#1F6F8B", weight: 2, fillOpacity: 0.08, dashArray: undefined },
  no_fly: { cssColor: "#B0266F", weight: 2, fillOpacity: 0.2, dashArray: undefined },
  obstacle: { cssColor: "#8a6d1f", weight: 2, fillOpacity: 0.18, dashArray: "2 4" },
} as const

export const INVALID_STYLE = {
  color: "#c62828",
  weight: 3,
  dashArray: "6 4",
  fillOpacity: 0.12,
} as const

export const POINT_COLOR = { launch_site: "#1F6F8B", reserve_site: "#B8700F" } as const

/** Легенда ИНТ.ФТ.6 — порядок и подписи те же, что в legacy. */
export const LEGEND_ITEMS: Array<{ layer: LayerName; color: string; label: string }> = [
  { layer: "launch_site", color: "#1F6F8B", label: "ВПП" },
  { layer: "reserve_site", color: "#B8700F", label: "Резервная площадка" },
  { layer: "airspace", color: "#1F6F8B", label: "Разрешенное пространство" },
  { layer: "no_fly", color: "#B0266F", label: "Бесполетная зона (с буфером)" },
  { layer: "obstacle", color: "#8a6d1f", label: "Высотное препятствие" },
]

/** Палитра маршрутов БВС. Индекс берётся от отсортированного списка id,
 *  поэтому цвет борта стабилен между рендерами и версиями плана. */
export const UAV_COLORS = ["#1F6F8B", "#B8700F", "#4E7F22", "#B0266F", "#5B4E9B", "#177F77"]

export function colorForUav(uavId: string, allIds: string[]): string {
  const index = [...allIds].sort().indexOf(uavId)
  return UAV_COLORS[(index < 0 ? 0 : index) % UAV_COLORS.length]!
}
