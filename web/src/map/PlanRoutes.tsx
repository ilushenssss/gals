/**
 * Маршруты плана на карте (ИНТ.ФТ.11, ИНТ.ФТ.13).
 *
 * Каждый борт — своим цветом из фиксированной палитры: маршрут вылета
 * пунктиром (перелеты), галсы съемки сплошной линией того же цвета, старт
 * вылета — маркером. Маршруты рисуются SVG — им нужны тултипы; галсы уходят
 * на canvas: интерактивность им не нужна, а сегментов на большой сцене
 * тысячи, и SVG на них заметно тормозит.
 *
 * Подсветка и скрытие борта из легенды — `setStyle` и снятие слоя, без
 * пересоздания.
 */
import L from "leaflet"
import { useEffect, useMemo } from "react"
import type { PlanDetail } from "@/api/client"
import { formatUtc } from "@/shared/format"
import { PANES, useMap } from "./MapProvider"
import { useFitBounds } from "./FitBounds"
import { useLayerGroup } from "./useLayerGroup"
import { colorForUav, cssColorForUav, useMapPalette } from "./style"
import { ICONS, textTooltip } from "./icons"

const canvasRenderer = L.canvas({ pane: PANES.coverage.name })
type Tagged = { _galsUav?: string; _galsKind?: "route" | "survey" | "start" }

type LineGeometry = { type: string; coordinates: number[][] }

function firstPoint(geometry: unknown): [number, number] | null {
  const line = geometry as LineGeometry | null
  const point = line?.type === "LineString" ? line.coordinates[0] : null
  return point && point.length >= 2 ? [point[1]!, point[0]!] : null
}

export function planUavIds(plan: PlanDetail | null): string[] {
  return plan ? [...new Set(plan.sorties.map((s) => s.uav_id))].sort() : []
}

export function PlanRoutes({
  plan,
  area,
  highlightUav,
  hiddenUavs,
}: {
  plan: PlanDetail | null
  area?: unknown | null
  highlightUav?: string | null
  hiddenUavs?: ReadonlySet<string>
}) {
  const map = useMap()
  const palette = useMapPalette()
  const routes = useLayerGroup()
  const areaGroup = useLayerGroup()

  const uavIds = useMemo(() => planUavIds(plan), [plan])

  useEffect(() => {
    if (!areaGroup) return
    areaGroup.clearLayers()
    if (!area) return
    areaGroup.addLayer(
      L.geoJSON({ type: "Feature", geometry: area, properties: {} } as never, {
        pane: PANES.area.name,
        interactive: false,
        style: { color: palette.area, weight: 1.5, dashArray: "3 4", fillOpacity: 0 },
      }),
    )
  }, [area, areaGroup, palette])

  useEffect(() => {
    if (!routes) return
    routes.clearLayers()
    if (!plan) return

    for (const sortie of plan.sorties) {
      const color = colorForUav(sortie.uav_id, uavIds, palette)
      const tag = (layer: L.Layer, kind: Tagged["_galsKind"]) => {
        Object.assign(layer as unknown as Tagged, { _galsUav: sortie.uav_id, _galsKind: kind })
        routes.addLayer(layer)
      }

      tag(
        L.geoJSON({ type: "Feature", geometry: sortie.survey_tracks_geojson, properties: {} } as never, {
          pane: PANES.coverage.name,
          interactive: false,
          // renderer не объявлен в типах GeoJSONOptions, но передаётся дальше
          // в PathOptions каждого слоя — именно так canvas и включается.
          style: { color, weight: 2, opacity: 0.9, renderer: canvasRenderer },
        } as L.GeoJSONOptions),
        "survey",
      )

      const route = L.geoJSON({ type: "Feature", geometry: sortie.track_geojson, properties: {} } as never, {
        pane: PANES.routes.name,
        style: { color, weight: 2.5, opacity: 0.95, dashArray: "7 6" },
      })
      route.bindTooltip(
        textTooltip([
          `${sortie.uav_id} · вылет ${sortie.sortie_index + 1}`,
          `${formatUtc(sortie.start_utc)} – ${formatUtc(sortie.end_utc)}`,
          `${(sortie.distance_m / 1000).toFixed(1).replace(".", ",")} км`,
        ]),
        { sticky: true },
      )
      tag(route, "route")

      const start = firstPoint(sortie.track_geojson)
      if (start) {
        const marker = L.marker(start, {
          pane: PANES.markers.name,
          icon: ICONS.start(cssColorForUav(sortie.uav_id, uavIds)),
          keyboard: false,
        })
        marker.bindTooltip(textTooltip([`Старт: ${sortie.uav_id}, вылет ${sortie.sortie_index + 1}`, formatUtc(sortie.start_utc)]))
        tag(marker, "start")
      }
    }
  }, [plan, uavIds, routes, palette])

  // Подсветка борта и скрытие из легенды — без пересоздания слоев.
  useEffect(() => {
    if (!routes || !map) return
    routes.eachLayer((layer) => {
      const { _galsUav: uav, _galsKind: kind } = layer as unknown as Tagged
      const hidden = Boolean(uav && hiddenUavs?.has(uav))
      if (hidden) {
        map.removeLayer(layer)
        return
      }
      if (!map.hasLayer(layer)) layer.addTo(map)
      const dim = Boolean(highlightUav) && uav !== highlightUav
      if (kind === "route") (layer as L.GeoJSON).setStyle({ opacity: dim ? 0.18 : 0.95, weight: dim ? 2 : uav === highlightUav ? 3.5 : 2.5 })
      if (kind === "survey") (layer as L.GeoJSON).setStyle({ opacity: dim ? 0.15 : 0.9 })
      if (kind === "start") (layer as L.Marker).setOpacity(dim ? 0.3 : 1)
    })
  }, [highlightUav, hiddenUavs, routes, map, plan, palette])

  const bounds = useMemo(() => {
    if (!plan) return null
    const all = L.latLngBounds([])
    if (area) {
      const areaBounds = L.geoJSON({ type: "Feature", geometry: area, properties: {} } as never).getBounds()
      if (areaBounds.isValid()) all.extend(areaBounds)
    }
    for (const sortie of plan.sorties) {
      const candidate = L.geoJSON({ type: "Feature", geometry: sortie.track_geojson, properties: {} } as never).getBounds()
      if (candidate.isValid()) all.extend(candidate)
    }
    return all.isValid() ? all : null
  }, [plan, area])

  useFitBounds(bounds, plan?.id ?? null)
  return null
}
