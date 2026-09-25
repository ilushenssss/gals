/**
 * Этап 5 «Подтверждение и экспорт» (ЭКС.ФТ.2–9, ИНТ.ФТ.16–18).
 *
 * До подтверждения — статус и одна из трех ситуаций: проверка пройдена;
 * все нарушения приняты (подтверждение «вопреки нарушениям»); подтверждать
 * нельзя. После — файлы по каждому борту и общий архив. Первая выгрузка
 * переводит план в «Выгружен»; после нее панель говорит, что план готов к
 * загрузке на борт (ИНТ.ФТ.18).
 *
 * Скачивание — обычными ссылками `<a download>`, а не `fetch` + blob: имена
 * файлов кириллические и приходят в `Content-Disposition` по RFC 5987.
 * Поэтому момент, когда сервер отметит выгрузку, клиенту не известен —
 * после клика план опрашивается, пока статус не сменится (не дольше 10 с).
 */
import { useState } from "react"
import { Link, useParams } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { ApiError, DEFAULT_USER_NAME, api, exportAllUrl, exportUrl, getUserName } from "@/api/client"
import { LegendSlot, Sidebar } from "@/app/AppShell"
import { EnvironmentLayers } from "@/map/EnvironmentLayers"
import { PlanRoutes, planUavIds } from "@/map/PlanRoutes"
import { LegendBox, UavLegend } from "@/map/Legend"
import { cssColorForUav } from "@/map/style"
import { Button, buttonClass } from "@/shared/ui/Button"
import { Banner, KeyValue, SideHead } from "@/shared/ui/Display"
import { Field } from "@/shared/ui/Field"
import { Icon } from "@/shared/ui/Icon"
import { Skeleton } from "@/shared/ui/States"
import { StatusBadge } from "@/shared/ui/StatusBadge"
import { useToast } from "@/shared/ui/Toasts"
import { formatDateTime, formatDuration, formatKm, plural } from "@/shared/format"
import { useToggleSet } from "@/shared/useToggleSet"
import { STEP, useTaskChrome, useTaskFlow } from "@/features/task/useTaskFlow"

const NO_HIDDEN: ReadonlySet<string> = new Set()
const POLL_LIMIT_MS = 10_000

export function ExportScreen() {
  const { taskId, planId } = useParams()
  const toast = useToast()
  const queryClient = useQueryClient()
  const [hiddenUavs, toggleUav] = useToggleSet()
  const [confirmedBy, setConfirmedBy] = useState(() => {
    const name = getUserName()
    return name === DEFAULT_USER_NAME ? "" : name
  })
  const [downloaded, setDownloaded] = useState<ReadonlySet<string>>(new Set())
  const [pollUntil, setPollUntil] = useState(0)

  const flow = useTaskFlow(taskId, planId)
  useTaskChrome(flow, STEP.export, taskId)
  const plan = useQuery({
    queryKey: ["plan", planId],
    queryFn: () => api.getPlan(planId!),
    enabled: Boolean(planId),
    refetchInterval: (query) => (query.state.data?.status !== "Выгружен" && Date.now() < pollUntil ? 1000 : false),
  })

  const confirm = useMutation({
    mutationFn: () => api.confirmPlan(planId!, confirmedBy.trim() || undefined),
    onSuccess: (summary) => {
      queryClient.invalidateQueries({ queryKey: ["plan", planId] })
      queryClient.invalidateQueries({ queryKey: ["plans", taskId] })
      queryClient.invalidateQueries({ queryKey: ["task", taskId] })
      toast(`План подтвержден: версия ${summary.version}`)
    },
    onError: (error: Error) => {
      // ЭКС.ФТ.9: подтвердил кто-то другой — сообщение называет его,
      // статус подтягиваем, подтверждение не повторяем.
      if (error instanceof ApiError && error.status === 409) {
        queryClient.invalidateQueries({ queryKey: ["plan", planId] })
        queryClient.invalidateQueries({ queryKey: ["plans", taskId] })
      }
      toast(error.message, true)
    },
  })

  const detail = plan.data ?? null
  const report = flow.report.data && flow.report.data.plan_id === planId ? flow.report.data : null
  const passed = report?.status === "Пройдена"
  const overridden = Boolean(report?.violations_acknowledged) && !passed
  const confirmable = passed || overridden
  const confirmed = detail?.status === "Подтвержден" || detail?.status === "Выгружен"
  const exported = detail?.status === "Выгружен" || downloaded.size > 0
  const uavIds = planUavIds(detail)
  const violationCount = report ? report.checks.reduce((n, c) => n + c.violations.length, 0) : 0

  const onDownload = (key: string) => {
    setDownloaded((current) => new Set(current).add(key))
    setPollUntil(Date.now() + POLL_LIMIT_MS)
    // Первый тик опроса — сразу, дальше refetchInterval.
    window.setTimeout(() => queryClient.invalidateQueries({ queryKey: ["plan", planId] }), 400)
  }

  let body
  if (plan.isPending || flow.report.isPending) body = <Skeleton />
  else if (!detail) body = null
  else if (!confirmed)
    body = (
      <>
        <KeyValue
          rows={[
            ["Вылеты", `${detail.sortie_count} · ${plural(uavIds.length, "борт", "борта", "бортов")}`],
            ["J1 · J2", <span className="mono">{formatDuration(detail.j1_s)} · {formatDuration(detail.j2_s)}</span>],
            ["Проверка", report ? <StatusBadge status={report.status} /> : "не выполнялась"],
            overridden ? ["Принято оператором", <span className="mono">{violationCount} из {violationCount}</span>] : null,
          ]}
        />
        {passed ? <Banner kind="ok">Проверка безопасности пройдена по этой версии плана.</Banner> : null}
        {overridden ? (
          <Banner kind="warn">
            <b>Все нарушения приняты.</b> План будет подтвержден с отметкой «вопреки нарушениям» — она попадет в журнал и в карточку плана.
          </Banner>
        ) : null}
        {!confirmable ? (
          <Banner kind="bad">
            Подтверждать нельзя: {report ? "по этой версии плана есть непринятые нарушения" : "проверка безопасности по этой версии не выполнялась"}. Пройдите проверку или
            примите каждое нарушение на экране проверки.
          </Banner>
        ) : null}
        <Field label="ФИО подтверждающего" hint="Попадет в журнал и в сообщение для второго подтверждающего.">
          <input className="input" value={confirmedBy} placeholder="Иванов Петр Сергеевич" disabled={!confirmable} onChange={(event) => setConfirmedBy(event.target.value)} />
        </Field>
        <div className="section">
          <div className="cap">После подтверждения</div>
          <p className="muted sm">
            План станет неизменяемым, откроется выгрузка KML и GeoJSON по каждому борту и общий архив. Изменить задачу после этого нельзя — только создать новую.
          </p>
        </div>
      </>
    )
  else
    body = (
      <>
        <KeyValue
          rows={[
            ["Подтвердил", detail.confirmed_by ?? "—"],
            ["Когда", <span className="mono">{formatDateTime(detail.confirmed_at)}</span>],
            detail.confirmed_with_overrides ? ["Особо", <span style={{ color: "var(--warn-ink)", fontWeight: 600 }}>вопреки нарушениям</span>] : null,
            detail.exported_at ? ["Первая выгрузка", <span className="mono">{formatDateTime(detail.exported_at)}</span>] : null,
          ]}
        />
        <div className="section">
          <div className="cap">Файлы по бортам</div>
          <ul className="list">
            {uavIds.map((uav) => {
              const sorties = detail.sorties.filter((s) => s.uav_id === uav)
              const distance = sorties.reduce((sum, s) => sum + s.distance_m, 0)
              const done = downloaded.has(`${uav}.kml`) || downloaded.has(`${uav}.geojson`)
              return (
                <li key={uav} className="item" style={{ padding: "8px 10px" }}>
                  <span className="sw" style={{ background: cssColorForUav(uav, uavIds) }} />
                  <span className="body-col">
                    <span className="t mono" style={{ fontSize: 13 }}>
                      {uav}
                    </span>
                    <span className="m">
                      {plural(sorties.length, "вылет", "вылета", "вылетов")} · {formatKm(distance)}
                    </span>
                  </span>
                  {done ? <StatusBadge tone="ok">скачан</StatusBadge> : null}
                  <a className={buttonClass({ size: "sm" })} href={exportUrl(planId!, "kml", uav)} download aria-label={`${uav}: скачать KML`} onClick={() => onDownload(`${uav}.kml`)}>
                    KML
                  </a>
                  <a className={buttonClass({ size: "sm" })} href={exportUrl(planId!, "geojson", uav)} download aria-label={`${uav}: скачать GeoJSON`} onClick={() => onDownload(`${uav}.geojson`)}>
                    GeoJSON
                  </a>
                </li>
              )
            })}
          </ul>
          <a className={buttonClass({ variant: "primary", block: true })} href={exportAllUrl(planId!)} download onClick={() => onDownload("zip")}>
            <Icon name="download" size={15} />
            Скачать все · ZIP
          </a>
          <span className="muted" style={{ fontSize: 11.5 }}>
            Координаты WGS-84, время UTC. Архив воспроизводится байт в байт. Загрузка на борт — штатным ПО БВС.
          </span>
        </div>
        {exported ? (
          <Banner kind="ok" role="status">
            <b>План готов к выгрузке на борт.</b>{" "}
            <Link to="/catalog/history" style={{ fontWeight: 600 }}>
              Вернуться к списку задач
            </Link>
          </Banner>
        ) : null}
      </>
    )

  const footer =
    detail && !confirmed ? (
      <>
        <Link className={buttonClass({ variant: "ghost" })} to={`/tasks/${taskId}/plans/${planId}/safety`}>
          К проверке
        </Link>
        <Button variant="primary" className="push" disabled={!confirmable} busy={confirm.isPending} onClick={() => confirm.mutate()}>
          Подтвердить план
        </Button>
      </>
    ) : null

  return (
    <>
      <EnvironmentLayers environment={flow.environment.data ?? null} hidden={NO_HIDDEN} dimmed />
      <PlanRoutes plan={detail} area={flow.task.data?.area ?? null} hiddenUavs={hiddenUavs} />
      <Sidebar footer={footer}>
        <Link className="back" to={`/tasks/${taskId}/plans/${planId}/safety`}>
          <Icon name="back" size={14} />К проверке
        </Link>
        <SideHead
          title={confirmed ? "Экспорт" : "Подтверждение"}
          badge={detail ? <StatusBadge status={detail.status} /> : null}
          sub={detail ? `План версии ${detail.version}${confirmed ? " · неизменяемый" : ""}` : "…"}
        />
        {body}
      </Sidebar>
      <LegendSlot>
        <LegendBox>
          <UavLegend uavIds={uavIds} hidden={hiddenUavs} onToggle={toggleUav} />
        </LegendBox>
      </LegendSlot>
    </>
  )
}
