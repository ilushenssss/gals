/**
 * Одна карта Leaflet на всё приложение.
 *
 * Карта создаётся один раз над роутером и живёт вне React-дерева: слои — это
 * `L.LayerGroup`, которыми владеют компоненты-эффекты, рендерящие `null`.
 * `react-leaflet` не используется намеренно — он пересоздаёт слои на каждый
 * ререндер панели, а на плане с тысячами сегментов это заметно.
 *
 * Панели создаются здесь же и один раз: порядок отрисовки задаётся
 * z-index'ом панели, а не порядком добавления слоёв.
 *
 * Подложка по умолчанию отсутствует: пустой фон `--paper` плюс слои
 * обстановки. Так интерфейс сразу работает в худшем случае — в закрытом
 * контуре без интернета. `VITE_TILE_URL` включает тайлы (OSM, кеширующий
 * прокси или предзалитая пирамида в томе).
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

const TILE_URL = import.meta.env.VITE_TILE_URL as string | undefined
const TRANSPARENT_PIXEL =
  "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"

export function MapProvider({ children }: { children: ReactNode }) {
  const container = useRef<HTMLDivElement>(null)
  const [map, setMap] = useState<L.Map | null>(null)

  useEffect(() => {
    if (!container.current) return
    const instance = L.map(container.current, { zoomControl: true }).setView([55.75, 37.6], 6)

    for (const pane of Object.values(PANES)) {
      instance.createPane(pane.name).style.zIndex = String(pane.zIndex)
    }

    if (TILE_URL) {
      L.tileLayer(TILE_URL, {
        maxZoom: 19,
        // Без сети тайлы не грузятся — отдаём прозрачный пиксель вместо
        // серых квадратов с «крестиком», чтобы карта оставалась читаемой.
        errorTileUrl: TRANSPARENT_PIXEL,
        attribution: "&copy; OpenStreetMap",
      }).addTo(instance)
    }

    setMap(instance)
    return () => {
      instance.remove()
      setMap(null)
    }
  }, [])

  return (
    <MapContext.Provider value={map}>
      <div className="map" ref={container} />
      {map ? children : null}
    </MapContext.Provider>
  )
}
