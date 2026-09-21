/** Индикатор этапа ИНТ.ФТ.2 — пилюли, состояния и разделители как в legacy. */
export const STEPS = ["Обстановка", "Задача", "Планирование", "Проверка", "Экспорт"] as const

export function Stepper({
  active,
  done,
  onStep,
}: {
  active: number
  done: number[]
  onStep?: (index: number) => void
}) {
  return (
    <div className="stepper">
      {STEPS.map((label, index) => {
        const classes = ["step"]
        if (index === active) classes.push("active")
        if (done.includes(index)) classes.push("done")
        const clickable = onStep && (done.includes(index) || index === active)
        if (clickable) classes.push("clickable")
        return (
          <span key={label} style={{ display: "contents" }}>
            {index > 0 ? <span className="step-sep" /> : null}
            <div
              className={classes.join(" ")}
              onClick={clickable ? () => onStep?.(index) : undefined}
            >
              <span className="dot">{done.includes(index) ? "✓" : index + 1}</span>
              {label}
            </div>
          </span>
        )
      })}
    </div>
  )
}
