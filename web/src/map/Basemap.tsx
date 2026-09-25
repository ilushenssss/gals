/**
 * Подложка карты и ее переключатель.
 *
 * Провайдер — Esri (спутник World Imagery и схема World Street Map): по
 * требованию пользователя подложка явно не из данных OpenStreetMap, и
 * переключатель это решение не нарушает. Атрибуция Esri обязательна.
 *
 * «Без подложки» — режим закрытого контура: слои обстановки на фоне
 * `--map-bg`. Сборка для контура без интернета задает `VITE_BASEMAP=none`
 * (подложка по умолчанию) и, при наличии, `VITE_TILE_URL` — свою
 * предзалитую пирамиду или кеширующий прокси (тогда он становится «Схемой»).
 * Выбор оператора запоминается в браузере.
 */
import L from "leaflet"
import { useEffect, useState } from "react"
import { useMap } from "./MapProvider"

export type BasemapId = "none" | "schema" | "satellite"

const TRANSPARENT_PIXEL =
  "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
const CUSTOM_TILES = import.meta.env.VITE_TILE_URL as string | undefined
const DEFAULT = ((import.meta.env.VITE_BASEMAP as string | undefined) ?? "satellite") as BasemapId
const STORAGE_KEY = "gals.basemap"

const SOURCES: Record<Exclude<BasemapId, "none">, { url: string; attribution: string; maxNativeZoom?: number }> = {
  satellite: {
    url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    attribution: "Tiles &copy; Esri — Source: Esri, Maxar, Earthstar Geographics, and the GIS User Community",
  },
  schema: CUSTOM_TILES
    ? { url: CUSTOM_TILES, attribution: "Локальная подложка", maxNativeZoom: 13 }
    : {
        url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}",
        attribution: "Tiles &copy; Esri — Source: Esri, HERE, Garmin, FAO, NOAA, USGS",
      },
}

const LABELS: Array<[BasemapId, string]> = [
  ["none", "Без подложки"],
  ["schema", "Схема"],
  ["satellite", "Спутник"],
]

function readChoice(): BasemapId {
  try {
    const value = window.localStorage.getItem(STORAGE_KEY)
    if (value === "none" || value === "schema" || value === "satellite") return value
  } catch {
    // нет доступа к хранилищу — берем значение сборки
  }
  return DEFAULT
}

export function BasemapSwitch() {
  const map = useMap()
  const [choice, setChoice] = useState<BasemapId>(readChoice)

  useEffect(() => {
    if (!map || choice === "none") return
    const source = SOURCES[choice]
    const layer = L.tileLayer(source.url, {
      maxZoom: 19,
      maxNativeZoom: source.maxNativeZoom,
      // Без сети тайлы не грузятся — прозрачный пиксель вместо серых
      // квадратов с «крестиком», чтобы карта оставалась читаемой.
      errorTileUrl: TRANSPARENT_PIXEL,
      attribution: source.attribution,
    }).addTo(map)
    layer.bringToBack()
    return () => {
      map.removeLayer(layer)
    }
  }, [map, choice])

  const pick = (next: BasemapId) => {
    setChoice(next)
    try {
      window.localStorage.setItem(STORAGE_KEY, next)
    } catch {
      // выбор не переживет перезагрузку
    }
  }

  return (
    <div className="baseseg" role="group" aria-label="Подложка карты">
      {LABELS.map(([id, label]) => (
        <button key={id} type="button" aria-pressed={choice === id} onClick={() => pick(id)}>
          {label}
        </button>
      ))}
    </div>
  )
}
