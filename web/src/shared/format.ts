/** Форматирование — те же правила, что в legacy (пункт 45 чек-листа паритета). */

export function formatDuration(seconds: number): string {
  const total = Math.round(seconds)
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const rest = total % 60
  const parts: string[] = []
  if (hours > 0) parts.push(`${hours}ч`)
  if (hours > 0 || minutes > 0) parts.push(`${minutes}м`)
  parts.push(`${rest}с`)
  return parts.join(" ")
}

export function formatUtc(iso: string): string {
  return new Date(iso).toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  })
}
