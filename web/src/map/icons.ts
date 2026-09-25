/**
 * Маркеры карты как `L.divIcon` с inline-SVG (Таблица 1, ИНТ.ФТ.14).
 *
 * Цвета — CSS-переменные прямо в SVG: разметка маркера живет в документе, и
 * переменные разрешаются там же, поэтому маркеры следуют теме без
 * пересоздания. Подписи и тултипы строятся из текста через DOM, а не
 * склейкой HTML: имена объектов и тексты нарушений приходят из файлов
 * пользователя.
 */
import L from "leaflet"

const runway = `<svg width="48" height="20" viewBox="-24 -10 48 20"><g transform="rotate(-24)"><rect x="-20" y="-5" width="40" height="10" rx="2" style="fill:var(--map-site);stroke:var(--map-halo)" stroke-width="1.5"/><line x1="-14" y1="0" x2="14" y2="0" style="stroke:var(--map-halo)" stroke-width="1.5" stroke-dasharray="4 3"/></g></svg>`

const reserve = `<svg width="24" height="24" viewBox="-12 -12 24 24"><circle r="9" style="fill:var(--map-bg);stroke:var(--map-site)" stroke-width="2"/><path d="M-5,-5 L5,5 M5,-5 L-5,5" style="stroke:var(--map-site)" stroke-width="2"/></svg>`

const invalidPoint = `<svg width="24" height="24" viewBox="-12 -12 24 24"><circle r="9" style="fill:var(--map-invalid);stroke:var(--map-halo)" stroke-width="2"/><path d="M0,-5 V1 M0,4 V4.5" stroke="#fff" stroke-width="2.4" stroke-linecap="round"/></svg>`

function violation(accepted: boolean) {
  return `<svg width="34" height="34" viewBox="-17 -17 34 34" style="opacity:${accepted ? 0.4 : 1}"><circle r="16" style="fill:var(--map-violation)" opacity="0.18"/><circle r="11" style="fill:var(--map-violation);stroke:var(--map-halo)" stroke-width="2"/><path d="M0,-5.5 V1 M0,4.5 V5" stroke="#fff" stroke-width="2.4" stroke-linecap="round"/></svg>`
}

function start(color: string) {
  return `<svg width="20" height="20" viewBox="-10 -10 20 20"><circle r="7" style="fill:${color};stroke:var(--map-halo)" stroke-width="2.5"/></svg>`
}

const icon = (html: string, size: [number, number]) =>
  L.divIcon({ html, className: "map-icon", iconSize: size, iconAnchor: [size[0] / 2, size[1] / 2] })

export const ICONS = {
  runway: () => icon(runway, [48, 20]),
  reserve: () => icon(reserve, [24, 24]),
  invalidPoint: () => icon(invalidPoint, [24, 24]),
  violation: (accepted: boolean) => icon(violation(accepted), [34, 34]),
  start: (cssColor: string) => icon(start(cssColor), [20, 20]),
}

/** Тултип из строк текста: первая — полужирная. */
export function textTooltip(lines: Array<string | null | undefined>): HTMLElement {
  const root = document.createElement("div")
  lines.filter(Boolean).forEach((line, index) => {
    const row = document.createElement(index === 0 ? "b" : "div")
    row.textContent = line!
    root.appendChild(row)
  })
  return root
}
