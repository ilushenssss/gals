/**
 * Вкладки каталога: «Парк / История / Новая задача» — вход в систему до
 * выбора задачи. Горизонтальные, а не вертикальные, как в прежнем
 * интерфейсе: при панели 380 px вертикальная колонка съедала бы пятую часть
 * ширины под содержимое.
 */
import { NavLink } from "react-router-dom"
import { useHeaderContext } from "@/app/AppShell"

const TABS = [
  { to: "/catalog/fleet", label: "Парк" },
  { to: "/catalog/history", label: "История" },
  { to: "/catalog/new", label: "Новая задача" },
] as const

export function CatalogTabs() {
  // В каталоге нет ни задачи в шапке, ни степпера.
  useHeaderContext(null)
  return (
    <nav className="tabs top" aria-label="Разделы каталога">
      {TABS.map((tab) => (
        <NavLink key={tab.to} to={tab.to} className="tab">
          {tab.label}
        </NavLink>
      ))}
    </nav>
  )
}
