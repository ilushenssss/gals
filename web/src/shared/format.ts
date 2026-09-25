/** Форматирование чисел, длительностей и времени. */

export function formatDuration(seconds: number): string {
  const total = Math.round(seconds)
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const rest = total % 60
  const parts: string[] = []
  if (hours > 0) parts.push(`${hours}ч`)
  if (hours > 0 || minutes > 0) parts.push(`${minutes}м`)
  if (hours === 0) parts.push(`${rest}с`)
  return parts.join(" ")
}

/** Дата и время в локальном поясе браузера: «25.09, 11:40». */
export function formatUtc(iso: string): string {
  return new Date(iso).toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  })
}

/** Полная дата и время: «25.09.2026, 11:40». */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—"
  return new Date(iso).toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  })
}

/**
 * Время «чч:мм» в поясе задачи. Окно работ задается в местном времени
 * задачи (IANA-пояс), поэтому и расписание показывается в нем же — иначе
 * оператор в другом поясе видел бы вылет «вне окна».
 */
export function formatTime(iso: string, timeZone?: string | null): string {
  try {
    return new Date(iso).toLocaleTimeString("ru-RU", {
      hour: "2-digit",
      minute: "2-digit",
      timeZone: timeZone || "UTC",
    })
  } catch {
    return new Date(iso).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit", timeZone: "UTC" })
  }
}

/** Короткая подпись пояса: «MSK», «UTC+5». */
export function zoneLabel(timeZone?: string | null): string {
  if (!timeZone) return "UTC"
  try {
    const part = new Intl.DateTimeFormat("ru-RU", { timeZone, timeZoneName: "short" })
      .formatToParts(new Date())
      .find((p) => p.type === "timeZoneName")
    return part?.value ?? timeZone
  } catch {
    return timeZone
  }
}

export function formatKm(meters: number): string {
  return `${(meters / 1000).toFixed(1).replace(".", ",")} км`
}

export function formatNumber(value: number | null | undefined, digits = 1): string {
  if (value == null) return "—"
  return value.toFixed(digits).replace(".", ",")
}

/** «3 вылета», «5 вылетов». */
export function plural(n: number, one: string, few: string, many: string): string {
  const mod10 = n % 10
  const mod100 = n % 100
  if (mod10 === 1 && mod100 !== 11) return `${n} ${one}`
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return `${n} ${few}`
  return `${n} ${many}`
}

export function browserTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"
  } catch {
    return "UTC"
  }
}
