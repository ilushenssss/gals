/**
 * Тонкий клиент поверх сгенерированных типов (`schema.d.ts`).
 *
 * Генератор кода (orval, openapi-generator) здесь не годится: их
 * multipart-сериализаторы ломаются на `float | None = Form(None)` и на
 * `date`/`time`, и генерат пришлось бы патчить. Типы генерируются, функции
 * пишутся руками — это две сотни строк и полный контроль над формой запроса.
 */
import type { components } from "./schema"

type S = components["schemas"]

export type EnvironmentSummary = S["EnvironmentSummary"]
export type EnvironmentDetail = S["EnvironmentDetail"]
export type TaskSummary = S["TaskSummary"]
export type TaskDetail = S["TaskDetail"]
export type FleetSummary = S["FleetSummary"]
export type FleetDetail = S["FleetDetail"]
export type FleetInstance = S["FleetInstance"]
export type ModelSpec = S["ModelSpecOut"]
export type PlanSummary = S["PlanSummary"]
export type PlanDetail = S["PlanDetail"]
export type PlanSortie = S["PlanSortie"]
export type SafetyReport = S["SafetyReport"]
export type SafetyCheckOut = S["SafetyCheckOut"]
export type Violation = S["ViolationOut"]
export type PlanSortiePhase = S["PlanSortiePhase"]
export type JobInfo = S["JobInfo"]

export const USER_NAME_KEY = "gals.userName"
export const DEFAULT_USER_NAME = "Оператор"

export function getUserName(): string {
  try {
    return localStorage.getItem(USER_NAME_KEY)?.trim() || DEFAULT_USER_NAME
  } catch {
    return DEFAULT_USER_NAME
  }
}

export function setUserName(name: string): void {
  try {
    localStorage.setItem(USER_NAME_KEY, name.trim() || DEFAULT_USER_NAME)
  } catch {
    /* приватный режим браузера — имя просто не запомнится */
  }
}

/**
 * ФИО кириллические, а значение HTTP-заголовка обязано быть ASCII.
 * Бэкенд (api/deps.py) принимает процентное кодирование UTF-8 — шлем его.
 */
function userHeaders(): Record<string, string> {
  return { "X-User-Name": encodeURIComponent(getUserName()) }
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly detail?: unknown,
  ) {
    super(message)
  }
}

/**
 * Ошибка запуска расчета, за которой стоит уже записанный исход работы:
 * POST /api/plans ждет работу синхронно и отвечает 409 (лимит времени,
 * отмена, сбой) или 422 (задача невыполнима), если она кончилась без плана.
 */
export function isJobOutcome(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 409 || error.status === 422)
}

/** Ошибки валидации по полям — их отдает модуль «Задача» (ЗАД.ФТ.9). */
export type FieldIssue = { field: string; message: string }

export function fieldIssues(error: unknown): FieldIssue[] {
  if (!(error instanceof ApiError) || !Array.isArray(error.detail)) return []
  return (error.detail as FieldIssue[]).filter((i) => i && typeof i.field === "string")
}

function messageOf(detail: unknown, fallback: string): string {
  if (typeof detail === "string") return detail
  if (Array.isArray(detail)) {
    const issues = detail as Array<Partial<FieldIssue> & { msg?: string; loc?: string[] }>
    const parts = issues.map((i) => {
      if (i.field && i.message) return `${i.field}: ${i.message}`
      if (i.msg) return `${(i.loc ?? []).slice(-1)[0] ?? ""}: ${i.msg}`.trim()
      return ""
    })
    const text = parts.filter(Boolean).join("; ")
    if (text) return text
  }
  if (detail && typeof detail === "object" && "message" in detail) {
    return String((detail as { message: unknown }).message)
  }
  return fallback
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { ...userHeaders(), ...(init.headers ?? {}) },
  })
  if (!response.ok) {
    let detail: unknown
    try {
      detail = (await response.json()).detail
    } catch {
      detail = undefined
    }
    throw new ApiError(response.status, messageOf(detail, `Ошибка ${response.status}`), detail)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

/**
 * Сборка multipart-формы.
 *
 * Главное правило, которое нельзя регрессировать: `null`/`undefined`/пустая
 * строка означают «ключ не добавляем вовсе». Пустое значение в поле
 * `Form(None)` FastAPI разбирает как строку и отвечает 422 — vanilla-версия
 * делала это правильно, и здесь повторено намеренно.
 */
export function toFormData(values: Record<string, unknown>): FormData {
  const form = new FormData()
  for (const [key, value] of Object.entries(values)) {
    if (value === null || value === undefined) continue
    if (value instanceof Blob) {
      form.append(key, value, value instanceof File ? value.name : undefined)
      continue
    }
    const text = typeof value === "number" ? String(value) : String(value).trim()
    if (text === "") continue
    form.append(key, text)
  }
  return form
}

/** Область облета всегда уходит файлом — бэкенд требует `area_file` и на PUT. */
export function geojsonFile(geometry: unknown, name = "area.geojson"): File {
  return new File([JSON.stringify(geometry)], name, { type: "application/geo+json" })
}

// --- обстановка (ОБС) -------------------------------------------------------

export const api = {
  listEnvironments: () => request<EnvironmentSummary[]>("/api/environments"),
  getEnvironment: (id: string) => request<EnvironmentDetail>(`/api/environments/${id}`),
  uploadEnvironment: (name: string, file: File) =>
    request<EnvironmentSummary>("/api/environments", {
      method: "POST",
      body: toFormData({ name, file }),
    }),

  // --- парк БВС (ПБС) -------------------------------------------------------
  // Парков несколько, каждый именованный и со своей локацией: «текущего»
  // парка больше нет, задача называет свой.
  listFleets: () => request<FleetSummary[]>("/api/fleets"),
  getFleet: async (id: string): Promise<FleetDetail | null> => {
    try {
      return await request<FleetDetail>(`/api/fleets/${id}`)
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) return null
      throw error
    }
  },
  listModels: () => request<ModelSpec[]>("/api/fleets/models"),
  uploadFleet: (name: string, file: File, locationName?: string) =>
    request<FleetSummary>("/api/fleets", {
      method: "POST",
      body: toFormData({ name, location_name: locationName, file }),
    }),

  // --- задача (ЗАД) ---------------------------------------------------------
  listTasks: (environmentId?: string) =>
    request<TaskSummary[]>(
      environmentId ? `/api/tasks?environment_id=${encodeURIComponent(environmentId)}` : "/api/tasks",
    ),
  getTask: (id: string) => request<TaskDetail>(`/api/tasks/${id}`),
  createTask: (values: Record<string, unknown>) =>
    request<TaskSummary>("/api/tasks", { method: "POST", body: toFormData(values) }),
  updateTask: (id: string, values: Record<string, unknown>) =>
    request<TaskSummary>(`/api/tasks/${id}`, { method: "PUT", body: toFormData(values) }),

  // --- планирование (ПЛН) ---------------------------------------------------
  listPlans: (taskId: string) =>
    request<PlanSummary[]>(`/api/plans?task_id=${encodeURIComponent(taskId)}`),
  getPlan: (id: string) => request<PlanDetail>(`/api/plans/${id}`),
  /**
   * Запуск расчета. Ответ двойной: `200 PlanSummary`, если расчет уложился в
   * окно ожидания, иначе `202` с объектом работы. SPA обязана уметь оба —
   * в проде `wait_s = 0`, и приходит всегда второй.
   */
  createPlan: async (taskId: string): Promise<{ plan?: PlanSummary; job?: JobInfo }> => {
    const response = await fetch("/api/plans", {
      method: "POST",
      headers: userHeaders(),
      body: toFormData({ task_id: taskId }),
    })
    if (!response.ok) {
      const detail = await response.json().catch(() => ({}))
      throw new ApiError(response.status, messageOf(detail.detail, "Не удалось рассчитать план"), detail.detail)
    }
    const body = await response.json()
    return response.status === 202 ? { job: body.job as JobInfo } : { plan: body as PlanSummary }
  },

  // --- фоновый расчет (ПЛН.ФТ.5) --------------------------------------------
  getJob: (id: string) => request<JobInfo>(`/api/plan-jobs/${id}`),
  listJobs: (taskId: string) =>
    request<JobInfo[]>(`/api/plan-jobs?task_id=${encodeURIComponent(taskId)}`),
  cancelJob: (id: string) => request<JobInfo>(`/api/plan-jobs/${id}/cancel`, { method: "POST" }),

  // --- проверка безопасности (БЕЗ) ------------------------------------------
  runSafetyCheck: async (planId: string): Promise<{ report?: SafetyReport; job?: JobInfo }> => {
    const response = await fetch("/api/safety-checks", {
      method: "POST",
      headers: userHeaders(),
      body: toFormData({ plan_id: planId }),
    })
    if (!response.ok) {
      const detail = await response.json().catch(() => ({}))
      throw new ApiError(response.status, messageOf(detail.detail, "Не удалось выполнить проверку"), detail.detail)
    }
    const body = await response.json()
    return response.status === 202 ? { job: body.job as JobInfo } : { report: body as SafetyReport }
  },
  recheckSafety: (planId: string) =>
    request<SafetyReport>("/api/safety-checks/recheck", {
      method: "POST",
      body: toFormData({ plan_id: planId }),
    }),
  latestSafetyReport: async (planId: string): Promise<SafetyReport | null> => {
    try {
      return await request<SafetyReport>(
        `/api/safety-checks/latest?plan_id=${encodeURIComponent(planId)}`,
      )
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) return null
      throw error
    }
  },

  /** Принять или снять принятие всех нарушений отчета разом. */
  ignoreAllViolations: (reportId: string, ignored: boolean) =>
    request<SafetyReport>(`/api/safety-checks/${reportId}/violations/ignore-all`, {
      method: "POST",
      body: toFormData({ ignored }),
    }),

  ignoreViolation: (reportId: string, violationId: string, ignored: boolean) =>
    request<SafetyReport>(
      `/api/safety-checks/${reportId}/violations/${encodeURIComponent(violationId)}/ignore`,
      { method: "POST", body: toFormData({ ignored }) },
    ),

  // --- подтверждение и экспорт (ЭКС) ----------------------------------------
  // ФИО уходит полем формы: это контракт модуля. Заголовок X-User-Name
  // уходит тоже и служит запасным путём на стороне сервера.
  confirmPlan: (id: string, confirmedBy?: string) =>
    request<PlanSummary>(`/api/plans/${id}/confirm`, {
      method: "POST",
      body: toFormData({ confirmed_by: confirmedBy }),
    }),
}

/**
 * Ссылка на скачивание.
 *
 * Скачивание делается обычным `<a download>`, а не `fetch` + blob: браузер
 * сам разбирает `Content-Disposition` с RFC 5987 и сохраняет кириллическое
 * имя файла, тогда как blob-путь заставил бы придумывать имя на клиенте.
 * Заголовок `X-User-Name` у такой ссылки не уходит — журнал выгрузок на
 * сервере записывает ее без имени оператора.
 */
export function exportUrl(planId: string, format: "kml" | "geojson", uavId: string): string {
  return `/api/plans/${planId}/export/${format}/${encodeURIComponent(uavId)}`
}

export function exportAllUrl(planId: string): string {
  return `/api/plans/${planId}/export/zip`
}
