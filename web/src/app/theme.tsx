/**
 * Тема интерфейса: системная по умолчанию, ручной выбор запоминается.
 *
 * Выбор хранится в localStorage (доступ обернут — в приватном окне или при
 * запрещенных данных сайта он бросает исключение) и выставляется атрибутом
 * `data-theme` на <html>; при «как в системе» атрибута нет, и работает
 * медиавыражение из tokens.css. `resolved` нужен карте: цвета Leaflet
 * задаются значениями, а не CSS-переменными, и должны перечитываться при
 * смене темы — в том числе системной, без действий оператора.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react"
import type { ReactNode } from "react"
import { IconButton } from "@/shared/ui/Button"

export type ThemePref = "system" | "light" | "dark"
type ThemeState = { pref: ThemePref; resolved: "light" | "dark"; setPref: (pref: ThemePref) => void }

const STORAGE_KEY = "gals.theme"
const ThemeContext = createContext<ThemeState>({ pref: "system", resolved: "light", setPref: () => {} })

export const useTheme = () => useContext(ThemeContext)

function readPref(): ThemePref {
  try {
    const value = window.localStorage.getItem(STORAGE_KEY)
    return value === "light" || value === "dark" ? value : "system"
  } catch {
    return "system"
  }
}

const media = () => window.matchMedia("(prefers-color-scheme: dark)")

// Атрибут ставится синхронно, а не эффектом: эффекты детей (слоев карты)
// выполняются раньше эффекта провайдера и прочитали бы цвета прежней темы.
function applyPref(pref: ThemePref) {
  const root = document.documentElement
  if (pref === "system") root.removeAttribute("data-theme")
  else root.setAttribute("data-theme", pref)
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [pref, setPrefState] = useState<ThemePref>(() => {
    const initial = readPref()
    applyPref(initial)
    return initial
  })
  const [systemDark, setSystemDark] = useState(() => media().matches)

  useEffect(() => {
    const query = media()
    const onChange = () => setSystemDark(query.matches)
    query.addEventListener("change", onChange)
    return () => query.removeEventListener("change", onChange)
  }, [])

  const setPref = useCallback((next: ThemePref) => {
    applyPref(next)
    setPrefState(next)
    try {
      if (next === "system") window.localStorage.removeItem(STORAGE_KEY)
      else window.localStorage.setItem(STORAGE_KEY, next)
    } catch {
      // выбор просто не переживет перезагрузку
    }
  }, [])

  const resolved = pref === "system" ? (systemDark ? "dark" : "light") : pref
  const value = useMemo(() => ({ pref, resolved, setPref }), [pref, resolved, setPref])
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}

const NEXT: Record<ThemePref, ThemePref> = { system: "light", light: "dark", dark: "system" }
const LABEL: Record<ThemePref, string> = {
  system: "Тема: как в системе",
  light: "Тема: светлая",
  dark: "Тема: темная",
}

export function ThemeToggle() {
  const { pref, setPref } = useTheme()
  return (
    <IconButton
      icon={pref === "system" ? "auto" : pref === "light" ? "sun" : "moon"}
      label={`${LABEL[pref]} — переключить`}
      onClick={() => setPref(NEXT[pref])}
    />
  )
}
