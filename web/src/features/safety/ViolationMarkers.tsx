/**
 * Маркеры нарушений на карте (ИНТ.ФТ.14).
 *
 * Нарушение адресуется вылетом: оркестратор проверки приписывает каждому
 * сообщению подпись «{БВС} · вылет {N}» (БЕЗ.ФТ.4), по ней и находится
 * маршрут. Точки у нарушения пока нет — проверки возвращают причину, но не
 * координату, — поэтому маркер ставится в начало маршрута, а тултип
 * показывает все причины по этому вылету. Когда проверки начнут возвращать
 * точку, поменяется только источник координаты.
 */
import L from "leaflet"
import { useEffect } from "react"
import type { PlanDetail, SafetyReport } from "@/api/client"
import { PANES } from "@/map/MapProvider"
import { useLayerGroup } from "@/map/useLayerGroup"

export function ViolationMarkers({
  plan,
  report,
}: {
  plan: PlanDetail | null
  report: SafetyReport | null
}) {
  const group = useLayerGroup()

  useEffect(() => {
    if (!group) return
    group.clearLayers()
    if (!plan || !report || report.status === "Пройдена") return

    const byUav = new Map<string, string[]>()
    for (const check of report.checks) {
      if (check.passed) continue
      for (const violation of check.violations) {
        for (const sortie of plan.sorties) {
          const label = `${sortie.uav_id} · вылет ${sortie.sortie_index + 1}`
          if (!violation.startsWith(`${label}:`)) continue
          const key = `${sortie.uav_id}#${sortie.sortie_index}`
          const reason = `${check.label}: ${violation.slice(label.length + 1).trim()}`
          byUav.set(key, [...(byUav.get(key) ?? []), reason])
        }
      }
    }

    for (const sortie of plan.sorties) {
      const key = `${sortie.uav_id}#${sortie.sortie_index}`
      const reasons = byUav.get(key)
      if (!reasons?.length) continue
      const coords = (sortie.track_geojson as { coordinates?: number[][] }).coordinates
      const first = coords?.[0]
      if (!first) continue
      const marker = L.circleMarker([first[1]!, first[0]!], {
        pane: PANES.markers.name,
        radius: 9,
        color: "#fff",
        weight: 2,
        fillColor: "#c62828",
        fillOpacity: 1,
      })
      marker.bindTooltip(
        `<b>${sortie.uav_id} · вылет ${sortie.sortie_index + 1}</b><br>${reasons.join("<br>")}`,
      )
      group.addLayer(marker)
    }
  }, [plan, report, group])

  return null
}
