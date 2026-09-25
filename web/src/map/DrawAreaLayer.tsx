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
import { useMapPalette } from "./style"

/** Вершина — кружок с номером; последняя залита, чтобы было видно, от какой
 *  точки пойдет следующий отрезок. */
function vertexIcon(index: number, last: boolean) {
  const fill = last ? "var(--map-area)" : "var(--surface)"
  const text = last ? "#fff" : "var(--text)"
  return L.divIcon({
    className: "map-icon",
    iconSize: [22, 22],
    iconAnchor: [11, 11],
    html: `<svg width="22" height="22" viewBox="-11 -11 22 22"><circle r="9" style="fill:${fill};stroke:var(--map-area)" stroke-width="2.5"/><text y="4" text-anchor="middle" font-size="11" font-weight="700" font-family="Onest, sans-serif" style="fill:${text}">${index + 1}</text></svg>`,
  })
}

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
  const palette = useMapPalette()
  const handler = useRef(onAdd)
  handler.current = onAdd

  useEffect(() => {
    if (!map || !active) return
    // 6 знаков после запятой — около 10 см: точнее клика мышью не бывает.
    const round = (value: number) => Math.round(value * 1e6) / 1e6
    const onClick = (event: L.LeafletMouseEvent) =>
      handler.current({ lat: round(event.latlng.lat), lng: round(event.latlng.lng) })
    map.on("click", onClick)
    const container = map.getContainer()
    container.style.cursor = "crosshair"
    // Во время рисования слои обстановки не перехватывают мышь: иначе
    // полигон пространства ловит наведение и всплывает тултип поверх точки.
    container.classList.add("is-drawing")
    return () => {
      map.off("click", onClick)
      container.style.cursor = ""
      container.classList.remove("is-drawing")
    }
  }, [map, active])

  useEffect(() => {
    if (!group) return
    group.clearLayers()
    if (!active) return

    points.forEach((point, index) => {
      group.addLayer(
        L.marker([point.lat, point.lng], {
          pane: PANES.markers.name,
          icon: vertexIcon(index, index === points.length - 1),
          interactive: false,
          keyboard: false,
        }),
      )
    })

    if (points.length >= 3) {
      group.addLayer(
        L.polygon(
          points.map((p) => [p.lat, p.lng] as [number, number]),
          { pane: PANES.area.name, color: palette.area, fillColor: palette.area, weight: 2.5, fillOpacity: 0.15, interactive: false },
        ),
      )
    } else if (points.length === 2) {
      group.addLayer(
        L.polyline(
          points.map((p) => [p.lat, p.lng] as [number, number]),
          { pane: PANES.area.name, color: palette.area, weight: 2.5, dashArray: "5 5", interactive: false },
        ),
      )
    }
  }, [active, points, group, palette])

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
