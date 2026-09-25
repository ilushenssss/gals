/**
 * Одна карта Leaflet на всё приложение.
 *
 * Карта создаётся один раз над роутером и живёт вне React-дерева: слои — это
 * `L.LayerGroup`, которыми владеют компоненты-эффекты, рендерящие `null`.
 * `react-leaflet` не используется намеренно — он пересоздаёт слои на каждый
 * ререндер панели, а на плане с тысячами сегментов это заметно.
 *
 * Панели создаются здесь же и один раз: порядок отрисовки задаётся
 * z-index'ом панели, а не порядком добавления слоёв. Здесь же объявлены
 * SVG-паттерны заливок (штриховка БПЗ, точки препятствия): Leaflet ставит
 * `fillColor` атрибутом `fill`, и `url(#id)` из документа в нем работает.
 * Подложка — отдельный компонент (Basemap.tsx).
 */
import L from "leaflet"
import { createContext, useContext, useEffect, useRef, useState } from "react"
import type { ReactNode } from "react"

const MapContext = createContext<L.Map | null>(null)

export const useMap = () => useContext(MapContext)

export const PANES = {
  envFill: { name: "env-fill", zIndex: 400 },
  envLine: { name: "env-line", zIndex: 410 },
  area: { name: "area", zIndex: 420 },
  coverage: { name: "coverage", zIndex: 430 },
  routes: { name: "routes", zIndex: 450 },
  markers: { name: "markers", zIndex: 470 },
} as const

function PatternDefs() {
  return (
    <svg width="0" height="0" style={{ position: "absolute" }} aria-hidden="true" focusable="false">
      <defs>
        <pattern id="gals-hatch-nofly" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <line x1="0" y1="0" x2="0" y2="8" style={{ stroke: "var(--map-nofly)" }} strokeWidth="2" />
        </pattern>
        <pattern id="gals-dots-obstacle" width="6" height="6" patternUnits="userSpaceOnUse">
          <circle cx="3" cy="3" r="1.1" style={{ fill: "var(--map-obstacle)" }} />
        </pattern>
      </defs>
    </svg>
  )
}

export function MapProvider({ children }: { children: ReactNode }) {
  const container = useRef<HTMLDivElement>(null)
  const [map, setMap] = useState<L.Map | null>(null)

  useEffect(() => {
    if (!container.current) return
    const instance = L.map(container.current, { zoomControl: false }).setView([55.75, 37.6], 6)
    L.control.zoom({ position: "bottomright", zoomInTitle: "Приблизить", zoomOutTitle: "Отдалить" }).addTo(instance)
    instance.attributionControl.setPrefix(false)

    for (const pane of Object.values(PANES)) {
      instance.createPane(pane.name).style.zIndex = String(pane.zIndex)
    }

    setMap(instance)
    return () => {
      instance.remove()
      setMap(null)
    }
  }, [])

  return (
    <MapContext.Provider value={map}>
      <PatternDefs />
      <div className="map" ref={container} />
      {map ? children : null}
    </MapContext.Provider>
  )
}
