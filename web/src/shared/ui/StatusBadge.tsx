/** Бейдж статуса: ОБС.ФТ.9, ЗАД, БЕЗ.ФТ.5 и ЭКС.ФТ.5 — один компонент. */
export type BadgeTone = "ok" | "bad" | "warn" | "draft" | "info"

const CLASS: Record<BadgeTone, string> = {
  ok: "status-badge ok",
  bad: "status-badge bad",
  warn: "status-badge warn",
  draft: "status-badge draft",
  info: "status-badge info",
}

export function StatusBadge({ tone, children }: { tone: BadgeTone; children: React.ReactNode }) {
  return <span className={CLASS[tone]}>{children}</span>
}
