/**
 * Слои обстановки (Таблица 1 модуля «Обстановка», ИНТ.ФТ.5, 7, 8).
 *
 * Одна `LayerGroup` на тип объекта, содержимое перестраивается только при
 * смене идентичности данных или палитры (тема): объекты приходят из кеша
 * TanStack Query и референциально стабильны, поэтому перерисовка боковой
 * панели не пересоздаёт полигоны. Скрытие слоя из легенды и приглушение —
 * отдельными эффектами, без пересоздания.
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
import { DIM_FACTOR, FILL_OPACITY, PATTERN, useMapPalette } from "./style"
import type { MapPalette } from "./style"
import { ICONS, textTooltip } from "./icons"

type GeoFeature = { type: string; properties?: Record<string, unknown>; geometry: unknown }
type Tagged = { _galsLayer?: string; _galsLabel?: boolean }

const POLYGON_LAYERS = ["airspace", "no_fly", "obstacle"] as const
const POINT_LAYERS = ["launch_site", "reserve_site"] as const
type PolygonLayer = (typeof POLYGON_LAYERS)[number]

const isValid = (feature: GeoFeature) => feature.properties?.["_valid"] !== false

function polygonStyle(layer: PolygonLayer, feature: GeoFeature, palette: MapPalette): L.PathOptions {
  if (!isValid(feature)) {
    return { color: palette.invalid, fillColor: palette.invalid, weight: 3, dashArray: "6 4", fillOpacity: 0.12 }
  }
  if (layer === "airspace") {
    return { color: palette.airspace, fillColor: palette.airspace, weight: 1.5, fillOpacity: FILL_OPACITY.airspace }
  }
  if (layer === "no_fly") {
    return { color: palette.nofly, fillColor: PATTERN.nofly, weight: 2, fillOpacity: FILL_OPACITY.no_fly }
  }
  return { color: palette.obstacle, fillColor: PATTERN.obstacle, weight: 1.5, dashArray: "2 3", fillOpacity: FILL_OPACITY.obstacle }
}

function objectName(layer: string, props: Record<string, unknown>): string {
  const fallback: Record<string, string> = {
    airspace: "Разрешенное пространство",
    no_fly: "Бесполетная зона",
    obstacle: "Препятствие",
    launch_site: "ВПП",
    reserve_site: "Резервная площадка",
  }
  return String(props["name"] ?? fallback[layer] ?? layer)
}

function heightRange(props: Record<string, unknown>): string | null {
  if (props["h_min"] == null && props["h_max"] == null) return null
  return `высота ${props["h_min"] ?? 0}–${props["h_max"] ?? "?"} м`
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
  const palette = useMapPalette()
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
      const obstacleLabels: L.Marker[] = []
      const group = L.geoJSON({ type: "FeatureCollection", features } as never, {
        pane: PANES.envFill.name,
        style: (feature) => polygonStyle(name, feature as GeoFeature, palette),
        onEachFeature: (feature, layer) => {
          const props = (feature.properties ?? {}) as Record<string, unknown>
          const valid = isValid(feature as GeoFeature)
          layer.bindTooltip(
            textTooltip([
              objectName(name, props),
              name === "no_fly" && props["safety_buffer_m"] != null ? `буфер ${props["safety_buffer_m"]} м` : null,
              name !== "airspace" ? heightRange(props) : null,
              valid ? null : String(props["_error"] ?? "объект с ошибкой"),
            ]),
            { sticky: true },
          )
          // Препятствию — постоянная подпись верхней границы (Таблица 1).
          // Отдельным невидимым маркером у восточного края: у слоя один
          // тултип, и подпись иначе заменила бы описание объекта.
          if (name === "obstacle" && valid && props["h_max"] != null) {
            const edge = (layer as L.Polygon).getBounds()
            const anchor = L.marker([edge.getCenter().lat, edge.getEast()], {
              pane: PANES.markers.name,
              icon: L.divIcon({ className: "map-icon", html: "", iconSize: [0, 0] }),
              interactive: false,
              keyboard: false,
            })
            anchor.bindTooltip(`↑ ${props["h_max"]} м`, {
              permanent: true,
              direction: "right",
              className: "map-label",
              offset: [4, 0],
            })
            obstacleLabels.push(anchor)
          }
          if (name === "no_fly" && props["_buffer_geojson"]) {
            buffers.addLayer(
              L.geoJSON({ type: "Feature", geometry: props["_buffer_geojson"], properties: {} } as never, {
                pane: PANES.envLine.name,
                interactive: false,
                style: { color: palette.nofly, weight: 1.5, dashArray: "6 5", fill: false },
              }),
            )
          }
        },
      })
      for (const label of obstacleLabels) group.addLayer(label)
      ;(group as unknown as Tagged)._galsLayer = name
      polygons.addLayer(group)
    }

    for (const name of POINT_LAYERS) {
      const features = layers[name] ?? []
      if (!features.length) continue
      const group = L.geoJSON({ type: "FeatureCollection", features } as never, {
        pane: PANES.markers.name,
        pointToLayer: (feature, latlng) => {
          const valid = isValid(feature as GeoFeature)
          const props = (feature.properties ?? {}) as Record<string, unknown>
          const marker = L.marker(latlng, {
            pane: PANES.markers.name,
            icon: !valid ? ICONS.invalidPoint() : name === "launch_site" ? ICONS.runway() : ICONS.reserve(),
            keyboard: false,
            title: objectName(name, props),
          })
          const label = L.tooltip({ permanent: true, direction: "bottom", className: "map-label", offset: [0, 8] })
          label.setContent(objectName(name, props))
          marker.bindTooltip(label)
          return marker
        },
      })
      ;(group as unknown as Tagged)._galsLayer = name
      points.addLayer(group)
    }
  }, [layers, polygons, buffers, points, palette])

  // Показ и скрытие слоёв из легенды (ИНТ.ФТ.6).
  useEffect(() => {
    if (!map || !polygons || !points || !buffers) return
    for (const group of [polygons, points]) {
      group.eachLayer((child) => {
        const name = (child as unknown as Tagged)._galsLayer
        const visible = !name || !hidden.has(name)
        if (visible && !map.hasLayer(child)) child.addTo(map)
        if (!visible) map.removeLayer(child)
      })
    }
    if (hidden.has("no_fly")) map.removeLayer(buffers)
    else if (!map.hasLayer(buffers)) buffers.addTo(map)
  }, [map, hidden, polygons, points, buffers, layers, palette])

  // Приглушение на экранах задачи, плана и проверки (ИНТ.ФТ.8): заливка
  // вдвое прозрачнее, постоянные подписи скрыты. Базовые значения всегда
  // берутся из констант, поэтому переключение идемпотентно.
  useEffect(() => {
    if (!polygons || !buffers || !points) return
    const factor = dimmed ? DIM_FACTOR : 1
    polygons.eachLayer((group) => {
      const name = (group as unknown as Tagged)._galsLayer as keyof typeof FILL_OPACITY | undefined
      const base = name ? FILL_OPACITY[name] : 0.1
      ;(group as L.LayerGroup).eachLayer((child) => {
        const path = child as L.Path & { feature?: GeoFeature }
        // У объекта с ошибкой своя прозрачность (INVALID), не слоя.
        const own = path.feature && !isValid(path.feature) ? 0.12 : base
        path.setStyle?.({ opacity: dimmed ? 0.6 : 1, fillOpacity: own * factor })
        const tip = path.getTooltip?.()
        if (tip?.options.permanent) {
          if (dimmed) path.closeTooltip()
          else path.openTooltip()
        }
      })
    })
    buffers.eachLayer((group) =>
      (group as L.LayerGroup).eachLayer((child) => (child as L.Path).setStyle?.({ opacity: dimmed ? 0.5 : 1 })),
    )
    points.eachLayer((group) =>
      (group as L.LayerGroup).eachLayer((child) => {
        const marker = child as L.Marker
        marker.setOpacity?.(dimmed ? 0.6 : 1)
        if (dimmed) marker.closeTooltip()
        else marker.openTooltip()
      }),
    )
  }, [dimmed, polygons, buffers, points, layers, palette, hidden])

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

/** Число объектов по слою — для легенды. */
export function layerCounts(environment: EnvironmentDetail | null): Record<string, number> {
  const layers = (environment?.layers ?? {}) as Record<string, unknown[]>
  return Object.fromEntries(Object.entries(layers).map(([key, list]) => [key, list.length]))
}
