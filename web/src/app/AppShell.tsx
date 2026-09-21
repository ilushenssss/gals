/**
 * Каркас приложения: шапка, одна карта на всё приложение, слоты панели и легенды.
 *
 * Карта создаётся здесь, **над** роутером, и живёт весь сеанс. Если бы её
 * создавал каждый экран, любой переход пересоздавал бы `L.Map` — а вместе с
 * ним панели, слои и вид: оператор терял бы масштаб и положение при каждом
 * шаге сценария. Экраны вместо этого:
 *
 * * объявляют свой шаг степпера через `useStep`;
 * * рисуют боковую панель и легенду порталом в слоты этого каркаса;
 * * добавляют слои карты обычными компонентами — они уже внутри контекста
 *   карты и рендерят `null`.
 */
import { createContext, useContext, useEffect, useMemo, useRef, useState } from "react"
import type { ReactNode } from "react"
import { createPortal } from "react-dom"
import { Link, Outlet } from "react-router-dom"
import { MapProvider } from "@/map/MapProvider"
import { Stepper } from "./Stepper"
import { UserName } from "./UserName"

type StepState = { active: number; done: number[]; onStep?: (index: number) => void }

type Slots = {
  sidebar: HTMLElement | null
  legend: HTMLElement | null
  overlay: HTMLElement | null
  setStep: (state: StepState) => void
}

const ShellContext = createContext<Slots>({
  sidebar: null,
  legend: null,
  overlay: null,
  setStep: () => {},
})

/** Объявить шаг сценария (ИНТ.ФТ.2). Значение живёт, пока экран смонтирован. */
export function useStep(active: number, done: number[], onStep?: (index: number) => void) {
  const { setStep } = useContext(ShellContext)
  const handler = useRef(onStep)
  handler.current = onStep
  const key = `${active}|${done.join(",")}`
  useEffect(() => {
    setStep({ active, done, onStep: (index) => handler.current?.(index) })
    // key схлопывает массив в строку: иначе новый литерал на каждый рендер
    // перезапускал бы эффект бесконечно.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, setStep])
}

export function Sidebar({ children }: { children: ReactNode }) {
  const { sidebar } = useContext(ShellContext)
  return sidebar ? createPortal(children, sidebar) : null
}

export function LegendSlot({ children }: { children: ReactNode }) {
  const { legend } = useContext(ShellContext)
  return legend ? createPortal(children, legend) : null
}

export function OverlaySlot({ children }: { children: ReactNode }) {
  const { overlay } = useContext(ShellContext)
  return overlay ? createPortal(children, overlay) : null
}

export function AppShell() {
  const sidebar = useRef<HTMLElement>(null)
  const legend = useRef<HTMLDivElement>(null)
  const overlay = useRef<HTMLDivElement>(null)
  const [mounted, setMounted] = useState(false)
  const [step, setStep] = useState<StepState>({ active: 0, done: [] })

  useEffect(() => setMounted(true), [])

  const slots = useMemo<Slots>(
    () => ({
      sidebar: mounted ? sidebar.current : null,
      legend: mounted ? legend.current : null,
      overlay: mounted ? overlay.current : null,
      setStep,
    }),
    [mounted],
  )

  return (
    <ShellContext.Provider value={slots}>
      <div className="app">
        <div className="stepper-bar">
          <Link to="/" className="brand">
            <span className="logo-chip">
              <img src="/logo.svg" alt="" />
            </span>
            <span className="brand-name">Галс</span>
          </Link>
          <Stepper active={step.active} done={step.done} onStep={step.onStep} />
          <UserName />
          <Link className="btn btn-fleet-nav" to="/fleet">
            Парк БВС
          </Link>
        </div>
        <div className="main">
          <MapProvider>
            <Outlet />
          </MapProvider>
          <div className="launch-overlay" ref={overlay} />
          <div className="legend-slot" ref={legend} />
          <aside className="sidebar" ref={sidebar} />
        </div>
      </div>
    </ShellContext.Provider>
  )
}
