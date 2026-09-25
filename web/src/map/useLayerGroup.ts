/** Стабильная `L.LayerGroup` на карте: создаётся один раз, снимается при размонтировании. */
import L from "leaflet"
import { useEffect, useRef } from "react"
import { useMap } from "./MapProvider"

export function useLayerGroup(): L.LayerGroup | null {
  const map = useMap()
  const group = useRef<L.LayerGroup | null>(null)

  if (group.current === null) group.current = L.layerGroup()

  useEffect(() => {
    if (!map || !group.current) return
    const layer = group.current
    layer.addTo(map)
    return () => {
      layer.clearLayers()
      map.removeLayer(layer)
    }
  }, [map])

  return group.current
}
