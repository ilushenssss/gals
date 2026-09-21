/**
 * Рисование области облёта кликами по карте (ЗАД.ФТ.9).
 *
 * Обработчик клика хранится в ref и вешается один раз: иначе он
 * перевешивался бы на каждый рендер формы, а вместе с ним пересоздавались бы
 * маркеры вершин.
 */
import L from "leaflet"
import { useEffect, useRef } from "react"
import { PANES, useMap } from "./MapProvider"
import { useLayerGroup } from "./useLayerGroup"

export type DrawPoint = { lat: number; lng: number }

export function DrawAreaLayer({
  active,
  points,
  onAdd,
}: {
  active: boolean
  points: DrawPoint[]
  onAdd: (point: DrawPoint) => void
}) {
  const map = useMap()
  const group = useLayerGroup()
  const handler = useRef(onAdd)
  handler.current = onAdd

  useEffect(() => {
    if (!map || !active) return
    const onClick = (event: L.LeafletMouseEvent) =>
      handler.current({ lat: event.latlng.lat, lng: event.latlng.lng })
    map.on("click", onClick)
    const container = map.getContainer()
    container.style.cursor = "crosshair"
    return () => {
      map.off("click", onClick)
      container.style.cursor = ""
    }
  }, [map, active])

  useEffect(() => {
    if (!group) return
    group.clearLayers()
    if (!active) return

    points.forEach((point, index) => {
      const marker = L.circleMarker([point.lat, point.lng], {
        pane: PANES.markers.name,
        radius: 5,
        color: "#B8700F",
        weight: 2,
        fillColor: "#fff",
        fillOpacity: 1,
      })
      marker.bindTooltip(String(index + 1), { permanent: true, direction: "top", offset: [0, -6] })
      group.addLayer(marker)
    })

    if (points.length >= 3) {
      group.addLayer(
        L.polygon(
          points.map((p) => [p.lat, p.lng] as [number, number]),
          { pane: PANES.area.name, color: "#B8700F", weight: 2.5, fillOpacity: 0.15 },
        ),
      )
    } else if (points.length === 2) {
      group.addLayer(
        L.polyline(
          points.map((p) => [p.lat, p.lng] as [number, number]),
          { pane: PANES.area.name, color: "#B8700F", weight: 2.5, dashArray: "4 4" },
        ),
      )
    }
  }, [active, points, group])

  return null
}

/** Замкнутый полигон GeoJSON из набора точек — то же, что делал legacy. */
export function pointsToPolygon(points: DrawPoint[]) {
  const ring = points.map((p) => [p.lng, p.lat])
  const first = ring[0]
  if (first) ring.push([first[0]!, first[1]!])
  return { type: "Polygon", coordinates: [ring] }
}

/** Обратное преобразование: вершины полигона задачи для правки. */
export function polygonToPoints(geometry: unknown): DrawPoint[] {
  const geom = geometry as { type?: string; coordinates?: number[][][] }
  if (geom?.type !== "Polygon" || !geom.coordinates?.[0]) return []
  const ring = geom.coordinates[0]
  return ring.slice(0, ring.length - 1).map((c) => ({ lat: c[1]!, lng: c[0]! }))
}
