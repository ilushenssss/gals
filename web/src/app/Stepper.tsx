/**
 * Индикатор этапа ИНТ.ФТ.2 и ИНТ.ФТ.20.
 *
 * Пройденные этапы — с галочкой и доступны для возврата; текущий выделен;
 * последующие недоступны. Этап, пройденный по данным, которые с тех пор
 * изменились (задачу отредактировали после расчета), помечен «!» и
 * пунктиром: он доступен, но его нужно пройти заново.
 */
export const STEPS = ["Обстановка", "Задача", "Планирование", "Проверка", "Экспорт"] as const

export function Stepper({
  active,
  done,
  stale,
  onStep,
}: {
  active: number
  done: number[]
  stale: number[]
  onStep?: (index: number) => void
}) {
  return (
    <nav className="stepper" aria-label="Этапы сценария">
      {STEPS.map((label, index) => {
        const isActive = index === active
        const isStale = !isActive && stale.includes(index)
        const isDone = !isActive && !isStale && done.includes(index)
        const reachable = isActive || isDone || isStale
        const classes = ["step", isActive ? "active" : "", isDone ? "done" : "", isStale ? "stale" : ""]
        return (
          <span key={label} style={{ display: "contents" }}>
            {index > 0 ? <span className="step-sep" aria-hidden="true" /> : null}
            <button
              type="button"
              className={classes.filter(Boolean).join(" ")}
              disabled={!reachable || !onStep}
              aria-current={isActive ? "step" : undefined}
              title={isStale ? "Данные изменились — этап нужно пройти заново" : undefined}
              onClick={() => onStep?.(index)}
            >
              <span className="n" aria-hidden="true">
                {isDone ? "✓" : isStale ? "!" : index + 1}
              </span>
              {label}
              {isDone ? <span className="visually-hidden"> — пройден</span> : null}
              {isStale ? <span className="visually-hidden"> — нужно пройти заново</span> : null}
            </button>
          </span>
        )
      })}
    </nav>
  )
}
