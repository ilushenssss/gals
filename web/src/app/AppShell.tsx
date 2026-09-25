/**
 * Каркас приложения: шапка, панель слева, одна карта на всё приложение.
 *
 * Карта создаётся здесь, **над** роутером, и живёт весь сеанс. Если бы её
 * создавал каждый экран, любой переход пересоздавал бы `L.Map` — а вместе с
 * ним панели, слои и вид: оператор терял бы масштаб и положение при каждом
 * шаге сценария. Экраны вместо этого:
 *
 * * объявляют свой шаг степпера через `useStep` (без вызова степпер скрыт —
 *   так в каталоге);
 * * рисуют боковую панель, легенду и оверлей порталами в слоты каркаса;
 * * добавляют слои карты обычными компонентами — они уже внутри контекста
 *   карты и рендерят `null`.
 */
import { createContext, useContext, useEffect, useMemo, useRef, useState } from "react"
import type { ReactNode } from "react"
import { createPortal } from "react-dom"
import { Link, Outlet } from "react-router-dom"
import { MapProvider } from "@/map/MapProvider"
import { BasemapSwitch } from "@/map/Basemap"
import { Icon } from "@/shared/ui/Icon"
import { buttonClass } from "@/shared/ui/Button"
import { Stepper } from "./Stepper"
import { UserName } from "./UserName"
import { ThemeToggle } from "./theme"

export type StepState = {
  active: number
  done: number[]
  /** Этапы, пройденные по устаревшим данным (ИНТ.ФТ.20). */
  stale?: number[]
  onStep?: (index: number) => void
}

export type HeaderContext = { title: string; exitTo: string; exitLabel?: string } | null

type Slots = {
  sidebar: HTMLElement | null
  legend: HTMLElement | null
  overlay: HTMLElement | null
  setStep: (state: StepState | null) => void
  setHeader: (context: HeaderContext) => void
}

const ShellContext = createContext<Slots>({
  sidebar: null,
  legend: null,
  overlay: null,
  setStep: () => {},
  setHeader: () => {},
})

/** Объявить шаг сценария (ИНТ.ФТ.2). Значение живёт, пока экран смонтирован. */
export function useStep(active: number, done: number[], onStep?: (index: number) => void, stale: number[] = []) {
  const { setStep } = useContext(ShellContext)
  const handler = useRef(onStep)
  handler.current = onStep
  const key = `${active}|${done.join(",")}|${stale.join(",")}`
  useEffect(() => {
    setStep({ active, done, stale, onStep: (index) => handler.current?.(index) })
    return () => setStep(null)
    // key схлопывает массивы в строку: иначе новый литерал на каждый рендер
    // перезапускал бы эффект бесконечно.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, setStep])
}

/** Заголовок задачи в шапке и выход в каталог. */
export function useHeaderContext(context: HeaderContext) {
  const { setHeader } = useContext(ShellContext)
  const key = context ? `${context.title}|${context.exitTo}|${context.exitLabel ?? ""}` : ""
  useEffect(() => {
    setHeader(context)
    return () => setHeader(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, setHeader])
}

/** Содержимое боковой панели: прокручиваемая часть и закрепленный низ с действиями. */
export function Sidebar({ children, footer }: { children: ReactNode; footer?: ReactNode }) {
  const { sidebar } = useContext(ShellContext)
  if (!sidebar) return null
  return createPortal(
    <>
      <div className="side-scroll">{children}</div>
      {footer ? <div className="side-foot">{footer}</div> : null}
    </>,
    sidebar,
  )
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
  const [step, setStep] = useState<StepState | null>(null)
  const [header, setHeader] = useState<HeaderContext>(null)

  useEffect(() => setMounted(true), [])

  const slots = useMemo<Slots>(
    () => ({
      sidebar: mounted ? sidebar.current : null,
      legend: mounted ? legend.current : null,
      overlay: mounted ? overlay.current : null,
      setStep,
      setHeader,
    }),
    [mounted],
  )

  return (
    <ShellContext.Provider value={slots}>
      <div className="shell">
        <header className="header">
          <Link to="/" className="brand" aria-label="Галс — в каталог">
            <span className="logo">
              <Icon name="logo" size={16} />
            </span>
            Галс
          </Link>
          {header ? (
            <div className="header-context">
              <span className="rule" />
              <Link className={buttonClass({ variant: "ghost", size: "sm" })} to={header.exitTo}>
                <Icon name="back" size={14} />
                {header.exitLabel ?? "Каталог"}
              </Link>
              <span className="header-title" title={header.title}>
                {header.title}
              </span>
            </div>
          ) : null}
          {step ? (
            <Stepper active={step.active} done={step.done} stale={step.stale ?? []} onStep={step.onStep} />
          ) : null}
          <div className="header-tools">
            <ThemeToggle />
            <UserName />
          </div>
        </header>
        <div className="body">
          <aside className="side" ref={sidebar} aria-label="Панель этапа" />
          <main className="stage">
            <MapProvider>
              <Outlet />
              <div className="map-chrome tr">
                <BasemapSwitch />
              </div>
            </MapProvider>
            <div className="map-chrome bl legend-slot" ref={legend} />
            <div className="overlay-slot" ref={overlay} />
          </main>
        </div>
      </div>
    </ShellContext.Provider>
  )
}
