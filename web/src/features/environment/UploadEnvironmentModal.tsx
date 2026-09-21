/** ОБС.ФТ.8: загрузка обстановки. Тексты и разметка — из legacy дословно. */
import { useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { api, type EnvironmentSummary } from "@/api/client"
import { Modal } from "@/shared/ui/Modal"

export function UploadEnvironmentModal({
  onClose,
  onUploaded,
}: {
  onClose: () => void
  onUploaded: (summary: EnvironmentSummary) => void
}) {
  const [error, setError] = useState<string | null>(null)
  const queryClient = useQueryClient()

  const upload = useMutation({
    mutationFn: ({ name, file }: { name: string; file: File }) => api.uploadEnvironment(name, file),
    onSuccess: (summary) => {
      queryClient.invalidateQueries({ queryKey: ["environments"] })
      onUploaded(summary)
    },
    onError: (err: Error) => setError(err.message || "Ошибка загрузки"),
  })

  return (
    <Modal title="Загрузка обстановки" onClose={onClose}>
      <form
        onSubmit={(event) => {
          event.preventDefault()
          setError(null)
          const form = new FormData(event.currentTarget)
          const file = form.get("file")
          if (!(file instanceof File) || !file.size) {
            setError("Выберите файл обстановки")
            return
          }
          upload.mutate({ name: String(form.get("name") ?? ""), file })
        }}
      >
        <label htmlFor="env-name">Название обстановки</label>
        <input
          type="text"
          id="env-name"
          name="name"
          required
          placeholder="Например, «Тестовая сцена 1»"
        />
        <label htmlFor="env-file">Файл GeoJSON</label>
        <input
          type="file"
          id="env-file"
          name="file"
          accept=".geojson,.json,application/geo+json,application/json"
          required
        />
        <p className="hint">
          FeatureCollection со слоями: ВПП, резервные площадки, разрешённое пространство,
          бесполетные зоны, высотные препятствия.
        </p>
        {error ? <p className="error">{error}</p> : null}
        <div className="actions">
          <button type="button" className="btn" onClick={onClose}>
            Отмена
          </button>
          <button type="submit" className="btn primary" disabled={upload.isPending}>
            Проверить и сохранить
          </button>
        </div>
      </form>
    </Modal>
  )
}
