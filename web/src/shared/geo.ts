/**
 * Клиентская геометрия для подсказок интерфейса — не для решений.
 *
 * Расчет и проверки остаются на бэкенде (метрическая проекция, PostGIS,
 * shapely). Здесь только то, что нужно показать до запроса: расстояние от
 * парка до района работ, пересекает ли нарисованная область БПЗ, мини-карта
 * области в списке задач. Буферы зон здесь не учитываются — поэтому это
 * предупреждение, а не запрет.
 */

type Position = number[]
type Ring = Position[]
type Geometry = { type?: string; coordinates?: unknown }

/** Полигоны геометрии как списки колец [lon, lat]. */
export function polygonsOf(geometry: unknown): Ring[][] {
  const geom = geometry as Geometry | null
  if (!geom?.coordinates) return []
  if (geom.type === "Polygon") return [geom.coordinates as Ring[]]
  if (geom.type === "MultiPolygon") return geom.coordinates as Ring[][]
  return []
}

export function pointsOf(geometry: unknown): Position[] {
  const geom = geometry as Geometry | null
  if (!geom?.coordinates) return []
  switch (geom.type) {
    case "Point":
      return [geom.coordinates as Position]
    case "LineString":
    case "MultiPoint":
      return geom.coordinates as Position[]
    case "Polygon":
    case "MultiLineString":
      return (geom.coordinates as Position[][]).flat()
    case "MultiPolygon":
      return (geom.coordinates as Position[][][]).flat(2)
    default:
      return []
  }
}

export type Bounds = { minLon: number; minLat: number; maxLon: number; maxLat: number }

export function boundsOf(geometries: unknown[]): Bounds | null {
  let b: Bounds | null = null
  for (const geometry of geometries) {
    for (const [lon, lat] of pointsOf(geometry) as [number, number][]) {
      if (!Number.isFinite(lon) || !Number.isFinite(lat)) continue
      if (!b) b = { minLon: lon, minLat: lat, maxLon: lon, maxLat: lat }
      else {
        b.minLon = Math.min(b.minLon, lon)
        b.maxLon = Math.max(b.maxLon, lon)
        b.minLat = Math.min(b.minLat, lat)
        b.maxLat = Math.max(b.maxLat, lat)
      }
    }
  }
  return b
}

/** Расстояние по дуге большого круга, км. */
export function distanceKm(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const rad = Math.PI / 180
  const dLat = (lat2 - lat1) * rad
  const dLon = (lon2 - lon1) * rad
  const a = Math.sin(dLat / 2) ** 2 + Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin(dLon / 2) ** 2
  return 6371 * 2 * Math.asin(Math.min(1, Math.sqrt(a)))
}

function pointInRing([x, y]: Position, ring: Ring): boolean {
  let inside = false
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i] as [number, number]
    const [xj, yj] = ring[j] as [number, number]
    if (yi > y! !== yj > y! && x! < ((xj - xi) * (y! - yi)) / (yj - yi) + xi) inside = !inside
  }
  return inside
}

function pointInPolygon(point: Position, polygon: Ring[]): boolean {
  const [outer, ...holes] = polygon
  if (!outer || !pointInRing(point, outer)) return false
  return !holes.some((hole) => pointInRing(point, hole))
}

function segmentsCross(a: Position, b: Position, c: Position, d: Position): boolean {
  const cross = (p: Position, q: Position, r: Position) => (q[0]! - p[0]!) * (r[1]! - p[1]!) - (q[1]! - p[1]!) * (r[0]! - p[0]!)
  const d1 = cross(c, d, a)
  const d2 = cross(c, d, b)
  const d3 = cross(a, b, c)
  const d4 = cross(a, b, d)
  return d1 * d2 < 0 && d3 * d4 < 0
}

function ringsCross(r1: Ring, r2: Ring): boolean {
  for (let i = 0; i + 1 < r1.length; i++) {
    for (let j = 0; j + 1 < r2.length; j++) {
      if (segmentsCross(r1[i]!, r1[i + 1]!, r2[j]!, r2[j + 1]!)) return true
    }
  }
  return false
}

/** Пересекаются ли две площадные геометрии (без учета буферов). */
export function areasIntersect(a: unknown, b: unknown): boolean {
  const pa = polygonsOf(a)
  const pb = polygonsOf(b)
  for (const polyA of pa) {
    for (const polyB of pb) {
      if (ringsCross(polyA[0] ?? [], polyB[0] ?? [])) return true
      if (polyA[0]?.[0] && pointInPolygon(polyA[0][0], polyB)) return true
      if (polyB[0]?.[0] && pointInPolygon(polyB[0][0], polyA)) return true
    }
  }
  return false
}

/** SVG-путь геометрии, вписанный в прямоугольник w×h — для мини-карты. */
export function thumbnailPath(geometry: unknown, w: number, h: number, pad = 4): string {
  const b = boundsOf([geometry])
  if (!b) return ""
  // Долгота сжимается косинусом широты, иначе на широте Москвы контур
  // выглядит вдвое шире, чем на карте.
  const k = Math.cos((((b.minLat + b.maxLat) / 2) * Math.PI) / 180)
  const spanX = Math.max((b.maxLon - b.minLon) * k, 1e-9)
  const spanY = Math.max(b.maxLat - b.minLat, 1e-9)
  const scale = Math.min((w - 2 * pad) / spanX, (h - 2 * pad) / spanY)
  const offX = (w - spanX * scale) / 2
  const offY = (h - spanY * scale) / 2
  return polygonsOf(geometry)
    .map((polygon) =>
      polygon
        .map(
          (ring) =>
            ring
              .map(([lon, lat], index) => {
                const x = offX + (lon! - b.minLon) * k * scale
                const y = offY + (b.maxLat - lat!) * scale
                return `${index ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`
              })
              .join("") + "Z",
        )
        .join(""),
    )
    .join("")
}
