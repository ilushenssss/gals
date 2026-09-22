/**
 * Маркеры нарушений на карте (ИНТ.ФТ.14).
 *
 * Нарушение само несёт координату «опасного момента» (`lat`/`lon`) — маркер
 * ставится именно туда, а не в начало маршрута вылета, как раньше, когда
 * проверки возвращали только текст и адрес приходилось выводить из подписи
 * «{БВС} · вылет N» разбором строки.
 *
 * Нарушения без точки (сводная строка «...и ещё N», доля непокрытой области)
 * на карте не показываются: у них нет осмысленного единственного места.
 * Принятые оператором нарушения меркнут, но не исчезают — риск принят, а не
 * устранён, и он должен оставаться на виду.
 */
import L from "leaflet"
import { useEffect } from "react"
import type { SafetyReport } from "@/api/client"
import { PANES } from "@/map/MapProvider"
import { useLayerGroup } from "@/map/useLayerGroup"

export function ViolationMarkers({ report }: { report: SafetyReport | null }) {
  const group = useLayerGroup()

  useEffect(() => {
    if (!group) return
    group.clearLayers()
    if (!report || report.status === "Пройдена") return

    for (const check of report.checks) {
      if (check.passed) continue
      for (const violation of check.violations) {
        if (violation.lat == null || violation.lon == null) continue
        const marker = L.circleMarker([violation.lat, violation.lon], {
          pane: PANES.markers.name,
          radius: 9,
          color: "#fff",
          weight: 2,
          fillColor: "#c62828",
          fillOpacity: violation.ignored ? 0.35 : 1,
        })
        marker.bindTooltip(
          `<b>${check.label}</b><br>${violation.message}` +
            (violation.ignored ? "<br><i>риск принят оператором</i>" : ""),
        )
        group.addLayer(marker)
      }
    }
  }, [report, group])

  return null
}
