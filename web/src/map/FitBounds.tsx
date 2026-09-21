/**
 * Подгонка вида под данные — только при смене `boundsKey`.
 *
 * В legacy `fitBounds` звался на каждый рендер и вырывал карту из-под
 * пользователя, стоило ему её подвинуть. Ключ — это «какие данные сейчас
 * показаны», а не «сколько раз мы отрисовались».
 */
import L from "leaflet"
import { useEffect, useRef } from "react"
import { useMap } from "./MapProvider"

export function useFitBounds(bounds: L.LatLngBounds | null, boundsKey: string | null) {
  const map = useMap()
  const applied = useRef<string | null>(null)

  useEffect(() => {
    if (!map || !bounds || !boundsKey || !bounds.isValid()) return
    if (applied.current === boundsKey) return
    applied.current = boundsKey
    map.fitBounds(bounds, { padding: [40, 40] })
  }, [map, bounds, boundsKey])
}
