/**
 * Маршруты плана на карте (ПЛН.ФТ.6).
 *
 * Маршруты рисуются SVG — им нужны тултипы с идентификатором БВС и номером
 * вылета. Галсы съёмки уходят на canvas: интерактивность им не нужна, а
 * сегментов на большой сцене тысячи, и SVG на них заметно тормозит.
 */
import L from "leaflet"
import { useEffect, useMemo } from "react"
import type { PlanDetail } from "@/api/client"
import { PANES } from "./MapProvider"
import { useFitBounds } from "./FitBounds"
import { useLayerGroup } from "./useLayerGroup"
import { colorForUav } from "./style"

const canvasRenderer = L.canvas({ pane: PANES.coverage.name })

export function PlanRoutes({
  plan,
  area,
  highlightUav,
}: {
  plan: PlanDetail | null
  area?: unknown | null
  highlightUav?: string | null
}) {
  const routes = useLayerGroup()
  const tracks = useLayerGroup()
  const areaGroup = useLayerGroup()

  const uavIds = useMemo(
    () => (plan ? [...new Set(plan.sorties.map((s) => s.uav_id))] : []),
    [plan],
  )

  useEffect(() => {
    if (!areaGroup) return
    areaGroup.clearLayers()
    if (!area) return
    areaGroup.addLayer(
      L.geoJSON({ type: "Feature", geometry: area, properties: {} } as never, {
        pane: PANES.area.name,
        style: { color: "#B8700F", weight: 1.5, dashArray: "4 4", fillColor: "#B8700F", fillOpacity: 0.05 },
      }),
    )
  }, [area, areaGroup])

  useEffect(() => {
    if (!routes || !tracks) return
    routes.clearLayers()
    tracks.clearLayers()
    if (!plan) return

    for (const sortie of plan.sorties) {
      const color = colorForUav(sortie.uav_id, uavIds)

      const survey = L.geoJSON(
        { type: "Feature", geometry: sortie.survey_tracks_geojson, properties: {} } as never,
        {
          pane: PANES.coverage.name,
          // renderer не объявлен в типах GeoJSONOptions, но передаётся дальше
          // в PathOptions каждого слоя — именно так canvas и включается.
          style: { color, weight: 1.5, opacity: 0.45, renderer: canvasRenderer },
        } as L.GeoJSONOptions,
      )
      tracks.addLayer(survey)

      const route = L.geoJSON(
        { type: "Feature", geometry: sortie.track_geojson, properties: {} } as never,
        { pane: PANES.routes.name, style: { color, weight: 3, opacity: 0.9 } },
      )
      route.bindTooltip(`${sortie.uav_id} · вылет ${sortie.sortie_index + 1}`)
      ;(route as unknown as { _galsUav: string })._galsUav = sortie.uav_id
      routes.addLayer(route)
    }
  }, [plan, uavIds, routes, tracks])

  // Подсветка выбранного борта — только setStyle, без пересоздания слоёв.
  useEffect(() => {
    if (!routes) return
    routes.eachLayer((layer) => {
      const uav = (layer as unknown as { _galsUav?: string })._galsUav
      const dim = Boolean(highlightUav) && uav !== highlightUav
      ;(layer as L.GeoJSON).setStyle({ opacity: dim ? 0.2 : 0.9, weight: dim ? 2 : 3 })
    })
  }, [highlightUav, routes, plan])

  const bounds = useMemo(() => {
    if (!plan) return null
    const all = L.latLngBounds([])
    if (area) {
      const areaBounds = L.geoJSON({ type: "Feature", geometry: area, properties: {} } as never).getBounds()
      if (areaBounds.isValid()) all.extend(areaBounds)
    }
    for (const sortie of plan.sorties) {
      const candidate = L.geoJSON(
        { type: "Feature", geometry: sortie.track_geojson, properties: {} } as never,
      ).getBounds()
      if (candidate.isValid()) all.extend(candidate)
    }
    return all.isValid() ? all : null
  }, [plan, area])

  useFitBounds(bounds, plan?.id ?? null)
  return null
}

export function UavLegend({
  uavIds,
  onHover,
}: {
  uavIds: string[]
  onHover?: (uavId: string | null) => void
}) {
  if (!uavIds.length) return null
  return (
    <div className="legend">
      {uavIds.map((id) => (
        <span
          key={id}
          style={{ display: "flex", alignItems: "center", gap: 7, margin: "4px 0", cursor: "default" }}
          onMouseEnter={() => onHover?.(id)}
          onMouseLeave={() => onHover?.(null)}
        >
          <span className="sw" style={{ background: colorForUav(id, uavIds) }} />
          {id}
        </span>
      ))}
    </div>
  )
}
