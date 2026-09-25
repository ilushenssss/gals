/**
 * Маркеры нарушений на карте (ИНТ.ФТ.14).
 *
 * Нарушение само несёт координату «опасного момента» (`lat`/`lon`) — маркер
 * ставится именно туда. Нарушения без точки (сводная строка «...и ещё N»,
 * доля непокрытой области) на карте не показываются: у них нет осмысленного
 * единственного места. Принятые оператором нарушения меркнут, но не
 * исчезают — риск принят, а не устранён, и он должен оставаться на виду.
 *
 * Тултип собирается из текста через DOM: сообщения содержат имена зон и
 * бортов из файлов пользователя, и склейка HTML была бы XSS-поверхностью.
 */
import L from "leaflet"
import { useEffect } from "react"
import type { SafetyReport } from "@/api/client"
import { PANES } from "@/map/MapProvider"
import { useLayerGroup } from "@/map/useLayerGroup"
import { ICONS, textTooltip } from "@/map/icons"

export function ViolationMarkers({
  report,
  highlight,
  onHover,
}: {
  report: SafetyReport | null
  /** id нарушения, подсвеченного в панели. */
  highlight?: string | null
  onHover?: (violationId: string | null) => void
}) {
  const group = useLayerGroup()

  useEffect(() => {
    if (!group) return
    group.clearLayers()
    if (!report) return

    for (const check of report.checks) {
      if (check.passed) continue
      for (const violation of check.violations) {
        if (violation.lat == null || violation.lon == null) continue
        const marker = L.marker([violation.lat, violation.lon], {
          pane: PANES.markers.name,
          icon: ICONS.violation(Boolean(violation.ignored)),
          keyboard: false,
          zIndexOffset: violation.id === highlight ? 1000 : 0,
        })
        marker.bindTooltip(
          textTooltip([check.label, violation.message, violation.ignored ? "риск принят оператором" : null]),
          { direction: "top", offset: [0, -14] },
        )
        marker.on("mouseover", () => onHover?.(violation.id))
        marker.on("mouseout", () => onHover?.(null))
        if (violation.id === highlight) marker.once("add", () => marker.openTooltip())
        group.addLayer(marker)
      }
    }
    // onHover — колбэк экрана; его смена не должна пересоздавать маркеры.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [report, group, highlight])

  return null
}
