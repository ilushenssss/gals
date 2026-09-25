/**
 * Загрузка обстановки (ОБС.ФТ.8) и парка БВС (ПБС).
 *
 * Обстановка принимается в GeoJSON или KML: бэкенд узнает KML по началу
 * файла. Файл с ошибками все равно сохраняется (со статусом «Содержит
 * ошибки»), поэтому окно в этом случае не закрывается молча, а показывает
 * итог и предлагает открыть обстановку — посмотреть проблемные объекты.
 */
import { useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { api } from "@/api/client"
import type { EnvironmentSummary, FleetSummary } from "@/api/client"
import { Button } from "@/shared/ui/Button"
import { Banner } from "@/shared/ui/Display"
import { Field } from "@/shared/ui/Field"
import { FilePicker } from "@/shared/ui/FilePicker"
import { Modal } from "@/shared/ui/Modal"
import { useToast } from "@/shared/ui/Toasts"

export function UploadEnvironmentModal({
  onClose,
  onUploaded,
  onOpen,
}: {
  onClose: () => void
  /** Обстановка корректна — окно закрывается, вызывающий выбирает ее. */
  onUploaded: (summary: EnvironmentSummary) => void
  /** Открыть обстановку с ошибками, чтобы увидеть проблемные объекты. */
  onOpen: (summary: EnvironmentSummary) => void
}) {
  const [name, setName] = useState("")
  const [file, setFile] = useState<File | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [withErrors, setWithErrors] = useState<EnvironmentSummary | null>(null)
  const queryClient = useQueryClient()
  const toast = useToast()

  const upload = useMutation({
    mutationFn: () => api.uploadEnvironment(name.trim(), file!),
    onSuccess: (summary) => {
      queryClient.invalidateQueries({ queryKey: ["environments"] })
      if (summary.status === "Корректна") {
        toast(`Обстановка «${summary.name}» загружена`)
        onUploaded(summary)
      } else {
        setWithErrors(summary)
      }
    },
    onError: (err: Error) => setError(err.message || "Ошибка загрузки"),
  })

  const footer = withErrors ? (
    <>
      <Button variant="ghost" onClick={onClose}>
        Закрыть
      </Button>
      <Button variant="primary" onClick={() => onOpen(withErrors)}>
        Открыть обстановку
      </Button>
    </>
  ) : (
    <>
      <Button variant="ghost" onClick={onClose}>
        Отмена
      </Button>
      <Button variant="primary" type="submit" form="env-upload" busy={upload.isPending}>
        Проверить и сохранить
      </Button>
    </>
  )

  return (
    <Modal title="Загрузка обстановки" onClose={onClose} footer={footer}>
      {withErrors ? (
        <Banner kind="bad" role="alert">
          <b>Файл проверен, ошибок: {withErrors.errors.length}.</b> Обстановка сохранена со статусом «Содержит
          ошибки» — ее можно открыть и посмотреть объекты, но нельзя выбрать для задачи.
        </Banner>
      ) : (
        <form
          id="env-upload"
          style={{ display: "contents" }}
          onSubmit={(event) => {
            event.preventDefault()
            setError(null)
            if (!name.trim()) return setError("Укажите название обстановки")
            if (!file) return setError("Выберите файл обстановки")
            upload.mutate()
          }}
        >
          <Field label="Название">
            <input
              className="input"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="Например, «Район Клин — сентябрь»"
            />
          </Field>
          <FilePicker
            accept=".geojson,.json,.kml,application/geo+json,application/json,application/vnd.google-earth.kml+xml"
            file={file}
            onPick={setFile}
            hint={
              <>
                GeoJSON со слоями launch_site, reserve_site, airspace, no_fly, obstacle — или KML: полигоны зон
                станут БПЗ, экструдированные — препятствиями, высоты берутся из описания.
              </>
            }
          />
          {error ? (
            <Banner kind="bad" role="alert">
              {error}
            </Banner>
          ) : null}
        </form>
      )}
    </Modal>
  )
}

export function UploadFleetModal({
  onClose,
  onUploaded,
}: {
  onClose: () => void
  onUploaded?: (summary: FleetSummary) => void
}) {
  const [name, setName] = useState("")
  const [location, setLocation] = useState("")
  const [file, setFile] = useState<File | null>(null)
  const [error, setError] = useState<string | null>(null)
  const queryClient = useQueryClient()
  const toast = useToast()

  const upload = useMutation({
    mutationFn: () => api.uploadFleet(name.trim(), file!, location.trim() || undefined),
    onSuccess: (summary) => {
      queryClient.invalidateQueries({ queryKey: ["fleets"] })
      toast(
        `Парк «${summary.name}» загружен: ${summary.ready_count} из ${summary.total} готовы`,
        summary.status === "Корректна" ? false : "warn",
      )
      onUploaded?.(summary)
      onClose()
    },
    onError: (err: Error) => setError(err.message || "Ошибка загрузки"),
  })

  return (
    <Modal
      title="Загрузка парка БВС"
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Отмена
          </Button>
          <Button variant="primary" type="submit" form="fleet-upload" busy={upload.isPending}>
            Проверить и сохранить
          </Button>
        </>
      }
    >
      <form
        id="fleet-upload"
        style={{ display: "contents" }}
        onSubmit={(event) => {
          event.preventDefault()
          setError(null)
          if (!name.trim()) return setError("Укажите название парка")
          if (!file) return setError("Выберите файл парка")
          upload.mutate()
        }}
      >
        <Field label="Название парка">
          <input className="input" value={name} onChange={(event) => setName(event.target.value)} placeholder="Клинский отряд" />
        </Field>
        <Field
          label="Расположение (необязательно)"
          hint="Координаты парка определяются по файлу — усредняются стоянки экземпляров. Название нужно только для подписи."
        >
          <input className="input" value={location} onChange={(event) => setLocation(event.target.value)} placeholder="Клин" />
        </Field>
        <FilePicker
          accept=".json,.csv,application/json,text/csv"
          file={file}
          onPick={setFile}
          hint="JSON или CSV: инвентарный номер, модель, статус готовности, базовая ВПП, координаты стоянки (location_lat, location_lon). Без координат хотя бы у одного экземпляра парк не принимается."
        />
        {error ? (
          <Banner kind="bad" role="alert">
            {error}
          </Banner>
        ) : null}
      </form>
    </Modal>
  )
}
