/**
 * Картографический стиль (Таблица 1 модуля «Обстановка», ИНТ.ФТ.5, 8, 11, 14).
 *
 * Leaflet принимает цвета значениями, а не CSS-переменными, поэтому палитра
 * читается из токенов `--map-*`/`--uav-*` на <html> и перечитывается при
 * смене темы (`useMapPalette` зависит от разрешенной темы). Слои, которые
 * берут палитру в зависимости эффекта, перекрашиваются сами.
 */
import { useMemo } from "react"
import { useTheme } from "@/app/theme"

export type LayerName = "airspace" | "no_fly" | "obstacle" | "launch_site" | "reserve_site"

export type MapPalette = {
  airspace: string
  nofly: string
  obstacle: string
  area: string
  invalid: string
  violation: string
  site: string
  halo: string
  uav: string[]
}

const UAV_SLOTS = 6

function readPalette(): MapPalette {
  const css = getComputedStyle(document.documentElement)
  const get = (name: string) => css.getPropertyValue(name).trim()
  return {
    airspace: get("--map-airspace"),
    nofly: get("--map-nofly"),
    obstacle: get("--map-obstacle"),
    area: get("--map-area"),
    invalid: get("--map-invalid"),
    violation: get("--map-violation"),
    site: get("--map-site"),
    halo: get("--map-halo"),
    uav: Array.from({ length: UAV_SLOTS }, (_, index) => get(`--uav-${index + 1}`)),
  }
}

export function useMapPalette(): MapPalette {
  const { resolved } = useTheme()
  // resolved — только ключ пересчета: сами значения лежат в CSS.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  return useMemo(readPalette, [resolved])
}

/** Заливки-паттерны, объявленные в MapProvider (штриховка БПЗ, точки препятствия). */
export const PATTERN = { nofly: "url(#gals-hatch-nofly)", obstacle: "url(#gals-dots-obstacle)" } as const

/** Базовая прозрачность заливки; при приглушении (ИНТ.ФТ.8) — вдвое меньше и без подписей. */
export const FILL_OPACITY = { airspace: 0.08, no_fly: 0.55, obstacle: 1 } as const
export const DIM_FACTOR = 0.5

/** Легенда ИНТ.ФТ.6: порядок и подписи. */
export const LEGEND_ITEMS: Array<{ layer: LayerName; label: string }> = [
  { layer: "launch_site", label: "ВПП" },
  { layer: "reserve_site", label: "Резервная площадка" },
  { layer: "airspace", label: "Разрешенное пространство" },
  { layer: "no_fly", label: "БПЗ и буфер" },
  { layer: "obstacle", label: "Высотное препятствие" },
]

/** Индекс борта берется от отсортированного списка id — цвет закреплен за
 *  экземпляром и стабилен между рендерами и версиями плана (ИНТ.ФТ.11). */
export function uavIndex(uavId: string, allIds: string[]): number {
  const index = [...allIds].sort().indexOf(uavId)
  return (index < 0 ? 0 : index) % UAV_SLOTS
}

export function colorForUav(uavId: string, allIds: string[], palette: MapPalette): string {
  return palette.uav[uavIndex(uavId, allIds)]!
}

/** CSS-значение цвета борта для разметки (легенда, таблица) — следует теме само. */
export function cssColorForUav(uavId: string, allIds: string[]): string {
  return `var(--uav-${uavIndex(uavId, allIds) + 1})`
}
