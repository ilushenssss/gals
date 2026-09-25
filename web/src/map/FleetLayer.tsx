/**
 * Парки и экземпляры БВС на карте (каталог, вкладка «Парк»).
 *
 * Без выбранного парка — точки всех парков; с выбранным — его экземпляры по
 * координатам стоянки, цвет по готовности, и пунктирный круг 150 км: дальше
 * этого парк не принимается для задачи (борта не долетят до области).
 */
import L from "leaflet"
import { useEffect, useMemo } from "react"
import type { FleetDetail, FleetSummary } from "@/api/client"
import { PANES, useMap } from "./MapProvider"
import { useFitBounds } from "./FitBounds"
import { useLayerGroup } from "./useLayerGroup"
import { useMapPalette } from "./style"
import { textTooltip } from "./icons"

const FLEET_RADIUS_M = 150_000

function dot(color: string, selected: boolean) {
  const r = selected ? 9 : 7
  const ring = selected ? `<circle r="15" style="fill:${color}" opacity="0.22"/>` : ""
  return L.divIcon({
    className: "map-icon",
    iconSize: [32, 32],
    iconAnchor: [16, 16],
    html: `<svg width="32" height="32" viewBox="-16 -16 32 32">${ring}<circle r="${r}" style="fill:${color};stroke:var(--map-halo)" stroke-width="2"/></svg>`,
  })
}

const STATUS_COLOR: Record<string, string> = { "Готов": "var(--ok)", "На обслуживании": "var(--warn)" }

export function FleetLayer({
  fleets,
  fleet,
  selected,
  onSelectFleet,
  onSelectInstance,
}: {
  fleets: FleetSummary[]
  fleet: FleetDetail | null
  selected: string | null
  onSelectFleet: (id: string) => void
  onSelectInstance: (inventory: string) => void
}) {
  const map = useMap()
  const palette = useMapPalette()
  const group = useLayerGroup()

  useEffect(() => {
    if (!group) return
    group.clearLayers()
    if (!fleet) {
      for (const item of fleets) {
        const marker = L.marker([item.location_lat, item.location_lon], {
          pane: PANES.markers.name,
          icon: dot("var(--accent)", false),
          title: item.name,
        })
        marker.bindTooltip(textTooltip([item.name, `${item.ready_count} из ${item.total} готовы`]))
        marker.on("click", () => onSelectFleet(item.id))
        group.addLayer(marker)
      }
      return
    }
    group.addLayer(
      L.circle([fleet.location_lat, fleet.location_lon], {
        pane: PANES.envLine.name,
        radius: FLEET_RADIUS_M,
        color: palette.site,
        weight: 1.5,
        dashArray: "4 5",
        fillOpacity: 0.03,
        interactive: false,
      }),
    )
    for (const instance of fleet.instances) {
      if (instance.location_lat == null || instance.location_lon == null) continue
      const isSelected = instance.inventory_number === selected
      const marker = L.marker([instance.location_lat, instance.location_lon], {
        pane: PANES.markers.name,
        icon: dot(isSelected ? "var(--accent)" : STATUS_COLOR[instance.status] ?? "var(--text-muted)", isSelected),
        title: instance.inventory_number,
        zIndexOffset: isSelected ? 1000 : 0,
      })
      marker.bindTooltip(textTooltip([instance.inventory_number, `${instance.model_name} · ${instance.status}`]))
      marker.on("click", () => onSelectInstance(instance.inventory_number))
      group.addLayer(marker)
    }
    // Колбэки меняются на каждый рендер экрана — маркеры от них не зависят.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [group, fleets, fleet, selected, palette])

  // Выбранный экземпляр — приблизить к нему (как flyTo в прежнем интерфейсе).
  useEffect(() => {
    if (!map || !fleet || !selected) return
    const instance = fleet.instances.find((i) => i.inventory_number === selected)
    if (instance?.location_lat == null || instance.location_lon == null) return
    map.flyTo([instance.location_lat, instance.location_lon], Math.max(map.getZoom(), 12), { duration: 0.6 })
  }, [map, fleet, selected])

  const bounds = useMemo(() => {
    const points = fleet
      ? fleet.instances.filter((i) => i.location_lat != null && i.location_lon != null).map((i) => [i.location_lat!, i.location_lon!] as [number, number])
      : fleets.map((f) => [f.location_lat, f.location_lon] as [number, number])
    if (!points.length) return null
    const b = L.latLngBounds(points)
    return b.isValid() ? b.pad(0.2) : null
  }, [fleet, fleets])

  useFitBounds(bounds, fleet ? `fleet-${fleet.id}` : fleets.length ? `fleets-${fleets.length}` : null)
  return null
}
