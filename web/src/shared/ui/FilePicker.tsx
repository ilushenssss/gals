/** Выбор файла: зона перетаскивания + кнопка, скрытый нативный input. */
import { useRef, useState } from "react"
import type { ReactNode } from "react"
import { Button } from "./Button"
import { Icon } from "./Icon"

export function FilePicker({
  accept,
  file,
  onPick,
  hint,
  label = "Файл",
}: {
  accept: string
  file: File | null
  onPick: (file: File | null) => void
  hint: ReactNode
  /** null — без подписи, когда поле уже внутри своей группы. */
  label?: string | null
}) {
  const input = useRef<HTMLInputElement>(null)
  const [drag, setDrag] = useState(false)
  return (
    <div className="field">
      {label ? <span className="lbl">{label}</span> : null}
      <div
        className={drag ? "dropzone drag" : "dropzone"}
        onDragOver={(event) => {
          event.preventDefault()
          setDrag(true)
        }}
        onDragLeave={() => setDrag(false)}
        onDrop={(event) => {
          event.preventDefault()
          setDrag(false)
          onPick(event.dataTransfer.files[0] ?? null)
        }}
      >
        <span style={{ color: "var(--accent)" }}>
          <Icon name={file ? "file" : "upload"} size={24} />
        </span>
        {file ? (
          <>
            <div style={{ fontWeight: 600, overflowWrap: "anywhere" }}>{file.name}</div>
            <div className="muted sm">{file.size < 1024 ? `${file.size} Б` : `${Math.round(file.size / 1024)} КБ`}</div>
          </>
        ) : (
          <div className="muted sm">Перетащите файл сюда</div>
        )}
        <Button size="sm" onClick={() => input.current?.click()}>
          {file ? "Выбрать другой" : "Выбрать файл"}
        </Button>
        <input
          ref={input}
          type="file"
          accept={accept}
          className="visually-hidden"
          tabIndex={-1}
          aria-label={label ?? "Файл"}
          onChange={(event) => onPick(event.target.files?.[0] ?? null)}
        />
      </div>
      <span className="hint">{hint}</span>
    </div>
  )
}
