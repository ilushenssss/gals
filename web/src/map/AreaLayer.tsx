/** Область облёта задачи (ИНТ.ФТ.9): оранжевый контур и заливка 15 %. */
import L from "leaflet"
import { useEffect, useMemo } from "react"
import { PANES } from "./MapProvider"
import { useFitBounds } from "./FitBounds"
import { useLayerGroup } from "./useLayerGroup"
import { useMapPalette } from "./style"

export function AreaLayer({
  area,
  boundsKey,
  outline = false,
}: {
  area: unknown | null
  boundsKey: string | null
  /** Только контур пунктиром — под маршрутами плана. */
  outline?: boolean
}) {
  const group = useLayerGroup()
  const palette = useMapPalette()

  const bounds = useMemo(() => {
    if (!area) return null
    const candidate = L.geoJSON({ type: "Feature", geometry: area, properties: {} } as never).getBounds()
    return candidate.isValid() ? candidate : null
  }, [area])

  useEffect(() => {
    if (!group) return
    group.clearLayers()
    if (!area) return
    group.addLayer(
      L.geoJSON({ type: "Feature", geometry: area, properties: {} } as never, {
        pane: PANES.area.name,
        interactive: false,
        style: outline
          ? { color: palette.area, weight: 1.5, dashArray: "3 4", fillOpacity: 0 }
          : { color: palette.area, weight: 2.5, fillColor: palette.area, fillOpacity: 0.15 },
      }),
    )
  }, [area, group, palette, outline])

  useFitBounds(bounds, boundsKey)
  return null
}
