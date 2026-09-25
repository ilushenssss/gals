/**
 * Имя оператора (ЗАД.ФТ.12, ЭКС.ФТ.9).
 *
 * Аутентификации по ТЗ нет, но сообщения о конфликте обязаны называть того,
 * кто изменил запись первым. Имя вводится один раз, хранится в localStorage и
 * уходит заголовком `X-User-Name` со всеми изменяющими запросами.
 */
import { useState } from "react"
import { DEFAULT_USER_NAME, getUserName, setUserName } from "@/api/client"

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean)
  return (parts[0]?.[0] ?? "?").toUpperCase() + (parts[1]?.[0] ?? "").toUpperCase()
}

export function UserName() {
  const [name, setName] = useState(getUserName)
  const [editing, setEditing] = useState(false)

  if (!editing) {
    return (
      <button
        className="btn sm user-chip"
        type="button"
        onClick={() => setEditing(true)}
        title="Имя оператора — попадает в журнал и в сообщения о конфликтах"
      >
        <span className="avatar" aria-hidden="true">
          {initials(name)}
        </span>
        <span className="name">{name}</span>
      </button>
    )
  }
  return (
    <input
      className="input sm"
      style={{ width: 220 }}
      aria-label="ФИО оператора"
      autoFocus
      defaultValue={name === DEFAULT_USER_NAME ? "" : name}
      placeholder="ФИО оператора"
      onBlur={(event) => {
        const value = event.currentTarget.value.trim() || DEFAULT_USER_NAME
        setUserName(value)
        setName(value)
        setEditing(false)
      }}
      onKeyDown={(event) => {
        if (event.key === "Enter") event.currentTarget.blur()
        if (event.key === "Escape") setEditing(false)
      }}
    />
  )
}
