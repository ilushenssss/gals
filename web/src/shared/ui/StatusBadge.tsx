/**
 * Бейдж статуса. Цвет выбирает одна карта «статус домена → тон», а не
 * экран: так «Корректна», «Пройдена» и «Подтвержден» везде зеленые, а
 * «Содержит ошибки» и «Есть нарушения» — везде одного красного.
 */
import type { ReactNode } from "react"

export type BadgeTone = "ok" | "bad" | "warn" | "draft" | "info"

const TONE_BY_STATUS: Record<string, BadgeTone> = {
  // обстановка, парк
  "Корректна": "ok",
  "Содержит ошибки": "bad",
  // экземпляр БВС
  "Готов": "ok",
  "Недоступен": "draft",
  "На обслуживании": "warn",
  // задача
  "Черновик": "draft",
  "Рассчитана": "info",
  "Подтверждена": "ok",
  // фоновая работа
  "В очереди": "info",
  "Выполняется": "info",
  "Завершен": "ok",
  "Остановлен по лимиту времени": "warn",
  "Ошибка": "bad",
  "Отменен": "draft",
  // проверка безопасности
  "Пройдена": "ok",
  "Есть нарушения": "bad",
  "В процессе автоматического пересчета": "warn",
  // план
  "Проверен": "info",
  "Подтвержден": "ok",
  "Выгружен": "ok",
}

export function toneOf(status: string | null | undefined): BadgeTone {
  if (!status) return "draft"
  return TONE_BY_STATUS[status] ?? "draft"
}

export function StatusBadge({
  status,
  tone,
  children,
}: {
  status?: string | null
  tone?: BadgeTone
  children?: ReactNode
}) {
  return <span className={`badge ${tone ?? toneOf(status)}`}>{children ?? status}</span>
}
