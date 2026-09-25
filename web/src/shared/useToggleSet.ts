/** Набор включенных-выключенных ключей: скрытые слои легенды, скрытые борта. */
import { useCallback, useState } from "react"

export function useToggleSet(initial: Iterable<string> = []): [ReadonlySet<string>, (key: string) => void] {
  const [set, setSet] = useState<ReadonlySet<string>>(() => new Set(initial))
  const toggle = useCallback((key: string) => {
    setSet((current) => {
      const next = new Set(current)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }, [])
  return [set, toggle]
}
