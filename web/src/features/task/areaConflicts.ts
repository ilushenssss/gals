/** Какие бесполетные зоны обстановки пересекает область облета (ИНТ.ФТ.9). */
import type { EnvironmentDetail } from "@/api/client"
import { areasIntersect } from "@/shared/geo"

type Feature = { geometry: unknown; properties?: Record<string, unknown> }

export type AreaConflict = { name: string; geometry: unknown }

export function areaConflicts(environment: EnvironmentDetail | null | undefined, area: unknown): AreaConflict[] {
  if (!environment || !area) return []
  const zones = ((environment.layers as Record<string, Feature[]>)["no_fly"] ?? []).filter((f) => f.properties?.["_valid"] !== false)
  return zones
    .filter((zone) => areasIntersect(area, zone.geometry))
    .map((zone) => ({ name: String(zone.properties?.["name"] ?? "без названия"), geometry: zone.geometry }))
}

/** Геометрия из файла области: Geometry, Feature или коллекция из одного объекта. */
export function geometryFromGeoJson(text: string): unknown | null {
  try {
    const data = JSON.parse(text) as { type?: string; geometry?: unknown; features?: Array<{ geometry?: unknown }> }
    if (data.type === "Feature") return data.geometry ?? null
    if (data.type === "FeatureCollection") return data.features?.length === 1 ? data.features[0]?.geometry ?? null : null
    return data.type === "Polygon" || data.type === "MultiPolygon" ? data : null
  } catch {
    return null
  }
}
