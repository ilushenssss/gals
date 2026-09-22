/** Экран 1 «Обстановка»: карта со слоями, сводка, ошибки, история. */
import { useMemo, useState } from "react"
import { useNavigate, useParams } from "react-router-dom"
import { useQuery } from "@tanstack/react-query"
import { api } from "@/api/client"
import { LegendSlot, OverlaySlot, Sidebar, useStep } from "@/app/AppShell"
import { EnvironmentLayers } from "@/map/EnvironmentLayers"
import { Legend } from "./Legend"
import { UploadEnvironmentModal } from "./UploadEnvironmentModal"

export function EnvironmentScreen() {
  const { environmentId } = useParams()
  const navigate = useNavigate()
  const [uploading, setUploading] = useState(false)
  const [hidden, setHidden] = useState<ReadonlySet<string>>(new Set())

  const history = useQuery({ queryKey: ["environments"], queryFn: api.listEnvironments })
  const environment = useQuery({
    queryKey: ["environment", environmentId],
    queryFn: () => api.getEnvironment(environmentId!),
    enabled: Boolean(environmentId),
  })

  const detail = environment.data ?? null
  const ok = detail?.status === "Корректна"
  const counts = detail?.counts

  const toggle = useMemo(
    () => (layer: string) =>
      setHidden((current) => {
        const next = new Set(current)
        if (next.has(layer)) next.delete(layer)
        else next.add(layer)
        return next
      }),
    [],
  )

  const historyBlock = (
    <div className="history">
      <h3>Загруженные обстановки</h3>
      <ul>
        {(history.data ?? []).map((item) => (
          <li key={item.id}>
            <a
              href={`/environments/${item.id}`}
              onClick={(event) => {
                event.preventDefault()
                navigate(`/environments/${item.id}`)
              }}
            >
              {item.name}
            </a>
          </li>
        ))}
      </ul>
      <button
        className="btn"
        style={{ width: "100%", marginTop: 10 }}
        onClick={() => setUploading(true)}
      >
        {detail ? "+ Загрузить ещё одну обстановку" : "+ Загрузить обстановку"}
      </button>
    </div>
  )

  const sidebar = detail ? (
    <>
      <p className="sb-title">{detail.name}</p>
      <p className="sb-sub">
        Загружена: {new Date(detail.uploaded_at).toLocaleString("ru-RU")}
      </p>
      <span className={ok ? "status-badge ok" : "status-badge bad"}>{detail.status}</span>
      <dl className="counts">
        <dt>ВПП</dt>
        <dd>{counts?.launch_site}</dd>
        <dt>Резервные площадки</dt>
        <dd>{counts?.reserve_site}</dd>
        <dt>Разрешенное пространство</dt>
        <dd>{counts?.airspace}</dd>
        <dt>Бесполетные зоны</dt>
        <dd>{counts?.no_fly}</dd>
        <dt>Высотные препятствия</dt>
        <dd>{counts?.obstacle}</dd>
      </dl>

      {!ok ? (
        <>
          <ul className="errors-list">
            {detail.errors.map((issue, index) => (
              <li key={index}>
                <span className="layer-tag">
                  {issue.layer}
                  {issue.name ? ` · ${issue.name}` : ""}
                </span>
                {issue.message}
              </li>
            ))}
          </ul>
          <button className="btn" disabled style={{ width: "100%" }}>
            Далее: Задача (недоступно — есть ошибки)
          </button>
        </>
      ) : (
        <button
          className="btn primary btn-next"
          style={{ marginTop: 4 }}
          onClick={() => navigate(`/environments/${detail.id}/tasks`)}
        >
          Далее: Задача
        </button>
      )}

      {historyBlock}
    </>
  ) : (
    // Обстановка не выбрана — но список уже загруженных всё равно нужен:
    // иначе, вернувшись в сервис со свежей вкладки, оператор видит пустой
    // экран и не может добраться до своих данных иначе как по прямой ссылке.
    <>
      <p className="sb-title">Обстановка</p>
      <p className="sb-sub">
        {history.data?.length
          ? "Выберите обстановку из списка или загрузите новую."
          : "Ни одной обстановки не загружено. Начните с файла GeoJSON."}
      </p>
      {historyBlock}
    </>
  )

  useStep(0, ok ? [0] : [], (index) => {
          if (index === 1 && ok && detail) navigate(`/environments/${detail.id}/tasks`)
        })

  return (
    <>
      <EnvironmentLayers environment={detail} hidden={hidden} />
      <Sidebar>{sidebar}</Sidebar>
      <LegendSlot>
        <Legend environment={detail} hidden={hidden} onToggle={toggle} />
      </LegendSlot>
      {detail ? null : (
        <OverlaySlot>
          <button className="btn-load" type="button" onClick={() => setUploading(true)}>
            Загрузить данные
          </button>
        </OverlaySlot>
      )}
      {uploading ? (
        <UploadEnvironmentModal
          onClose={() => setUploading(false)}
          onUploaded={(summary) => {
            setUploading(false)
            navigate(`/environments/${summary.id}`)
          }}
        />
      ) : null}
    </>
  )
}
