/**
 * Предупреждение ИНТ.ФТ.9: бесполетные зоны, которые пересекает область
 * облета, обводятся красным поверх остальных слоев. Сохранение задачи это
 * не блокирует — участок внутри зоны просто не будет сниматься.
 */
import L from "leaflet"
import { useEffect } from "react"
import { PANES } from "./MapProvider"
import { useLayerGroup } from "./useLayerGroup"
import { useMapPalette } from "./style"

export function ConflictLayer({ zones }: { zones: unknown[] }) {
  const group = useLayerGroup()
  const palette = useMapPalette()
  useEffect(() => {
    if (!group) return
    group.clearLayers()
    for (const geometry of zones) {
      group.addLayer(
        L.geoJSON({ type: "Feature", geometry, properties: {} } as never, {
          pane: PANES.routes.name,
          interactive: false,
          style: { color: palette.invalid, weight: 3, fillColor: palette.invalid, fillOpacity: 0.12 },
        }),
      )
    }
  }, [group, zones, palette])
  return null
}
