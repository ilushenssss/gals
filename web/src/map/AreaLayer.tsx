/** Область облёта задачи на карте (ЗАД.ФТ.11) — заливка, как в legacy. */
import L from "leaflet"
import { useEffect, useMemo } from "react"
import { PANES } from "./MapProvider"
import { useFitBounds } from "./FitBounds"
import { useLayerGroup } from "./useLayerGroup"

export function AreaLayer({
  area,
  boundsKey,
}: {
  area: unknown | null
  boundsKey: string | null
}) {
  const group = useLayerGroup()

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
        style: { color: "#B8700F", weight: 2.5, fillColor: "#B8700F", fillOpacity: 0.15 },
      }),
    )
  }, [area, group])

  useFitBounds(bounds, boundsKey)
  return null
}
