/**
 * Слои обстановки (Таблица 1 модуля «Обстановка»).
 *
 * Один компонент — одна `LayerGroup` на слой, содержимое перестраивается
 * только при смене идентичности данных: объекты приходят из кеша TanStack
 * Query и референциально стабильны, поэтому перерисовка боковой панели не
 * пересоздаёт полигоны.
 *
 * Буферы БПЗ берутся из `properties._buffer_geojson` — их считает бэкенд в
 * метрической проекции. Аппроксимировать буфер на клиенте нельзя: в градусах
 * он получится другим, и оператор увидит не ту зону, по которой идёт проверка.
 */
import L from "leaflet"
import { useEffect, useMemo } from "react"
import type { EnvironmentDetail } from "@/api/client"
import { useMap, PANES } from "./MapProvider"
import { useFitBounds } from "./FitBounds"
import { useLayerGroup } from "./useLayerGroup"
import { INVALID_STYLE, LAYER_STYLE, POINT_COLOR } from "./style"

type GeoFeature = { type: string; properties?: Record<string, unknown>; geometry: unknown }

const POLYGON_LAYERS = ["airspace", "no_fly", "obstacle"] as const
const POINT_LAYERS = ["launch_site", "reserve_site"] as const

function isValid(feature: GeoFeature): boolean {
  return feature.properties?.["_valid"] !== false
}

function polygonStyle(layer: (typeof POLYGON_LAYERS)[number], feature: GeoFeature) {
  if (!isValid(feature)) return INVALID_STYLE
  const style = LAYER_STYLE[layer]
  return {
    color: style.cssColor,
    fillColor: style.cssColor,
    weight: style.weight,
    fillOpacity: style.fillOpacity,
    dashArray: style.dashArray,
  }
}

function tooltipFor(layer: string, props: Record<string, unknown>): string {
  let label = String(props["name"] ?? (layer === "obstacle" ? "Препятствие" : layer))
  if (layer === "obstacle" && props["h_min"] != null) {
    label += ` (высота ${props["h_min"]}–${props["h_max"]} м)`
  }
  return label
}

export function EnvironmentLayers({
  environment,
  hidden,
  dimmed = false,
}: {
  environment: EnvironmentDetail | null
  hidden: ReadonlySet<string>
  dimmed?: boolean
}) {
  const map = useMap()
  const polygons = useLayerGroup()
  const buffers = useLayerGroup()
  const points = useLayerGroup()

  const layers = environment?.layers as Record<string, GeoFeature[]> | undefined

  useEffect(() => {
    if (!polygons || !buffers || !points) return
    polygons.clearLayers()
    buffers.clearLayers()
    points.clearLayers()
    if (!layers) return

    for (const name of POLYGON_LAYERS) {
      const features = layers[name] ?? []
      if (!features.length) continue
      const group = L.geoJSON(
        { type: "FeatureCollection", features } as never,
        {
          pane: PANES.envFill.name,
          style: (feature) => polygonStyle(name, feature as GeoFeature),
          onEachFeature: (feature, layer) => {
            const props = (feature.properties ?? {}) as Record<string, unknown>
            layer.bindTooltip(tooltipFor(name, props))
            if (name === "no_fly" && props["_buffer_geojson"]) {
              buffers.addLayer(
                L.geoJSON(
                  { type: "Feature", geometry: props["_buffer_geojson"], properties: {} } as never,
                  {
                    pane: PANES.envLine.name,
                    style: { color: "#B0266F", weight: 1.5, dashArray: "4 4", fill: false },
                  },
                ),
              )
            }
          },
        },
      )
      ;(group as unknown as { _galsLayer: string })._galsLayer = name
      polygons.addLayer(group)
    }

    for (const name of POINT_LAYERS) {
      const features = layers[name] ?? []
      if (!features.length) continue
      const group = L.geoJSON({ type: "FeatureCollection", features } as never, {
        pane: PANES.markers.name,
        pointToLayer: (feature, latlng) => {
          const valid = isValid(feature as GeoFeature)
          const color = valid ? POINT_COLOR[name] : "#c62828"
          const marker = L.circleMarker(latlng, {
            pane: PANES.markers.name,
            radius: 8,
            color: "#fff",
            weight: 2,
            fillColor: color,
            fillOpacity: 1,
          })
          const props = (feature.properties ?? {}) as Record<string, unknown>
          const label = String(
            props["name"] ?? (name === "launch_site" ? "ВПП" : "Резервная площадка"),
          )
          marker.bindTooltip(label, { permanent: true, direction: "bottom", offset: [0, 6] })
          return marker
        },
      })
      ;(group as unknown as { _galsLayer: string })._galsLayer = name
      points.addLayer(group)
    }
  }, [layers, polygons, buffers, points])

  // Показ и скрытие слоёв из легенды — отдельным эффектом, без пересоздания.
  useEffect(() => {
    if (!map || !polygons || !points || !buffers) return
    for (const group of [polygons, points]) {
      group.eachLayer((child) => {
        const name = (child as unknown as { _galsLayer?: string })._galsLayer
        const visible = !name || !hidden.has(name)
        const element = child as unknown as { _map?: unknown }
        if (visible && !element._map) group.addLayer(child)
        if (!visible) map.removeLayer(child as L.Layer)
      })
    }
    if (hidden.has("no_fly")) map.removeLayer(buffers)
    else buffers.addTo(map)
  }, [map, hidden, polygons, points, buffers])

  // Затемнение при работе с областью облёта: базовая прозрачность всегда
  // берётся из констант, поэтому включение-выключение идемпотентно.
  useEffect(() => {
    const factor = dimmed ? 0.35 : 1
    if (!polygons || !buffers || !points) return
    polygons.eachLayer((group) => {
      const name = (group as unknown as { _galsLayer?: string })._galsLayer
      const base = name && name in LAYER_STYLE
        ? LAYER_STYLE[name as keyof typeof LAYER_STYLE].fillOpacity
        : 0.1
      ;(group as L.LayerGroup).eachLayer((child) => {
        const path = child as L.Path
        if (path.setStyle) path.setStyle({ opacity: factor, fillOpacity: base * factor })
      })
    })
    buffers.eachLayer((group) =>
      (group as L.LayerGroup).eachLayer((child) => {
        const path = child as L.Path
        if (path.setStyle) path.setStyle({ opacity: factor })
      }),
    )
    points.eachLayer((group) =>
      (group as L.LayerGroup).eachLayer((child) => {
        const path = child as L.Path
        if (path.setStyle) path.setStyle({ opacity: factor, fillOpacity: factor })
      }),
    )
  }, [dimmed, polygons, buffers, points, layers])

  const bounds = useMemo(() => {
    if (!layers) return null
    const all = L.latLngBounds([])
    for (const name of [...POLYGON_LAYERS, ...POINT_LAYERS]) {
      for (const feature of layers[name] ?? []) {
        const candidate = L.geoJSON(feature as never).getBounds()
        if (candidate.isValid()) all.extend(candidate)
      }
    }
    return all.isValid() ? all : null
  }, [layers])

  useFitBounds(bounds, environment?.id ?? null)

  return null
}
