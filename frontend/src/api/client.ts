/**
 * Unified API client. All backend calls go through here; the Vite dev proxy
 * forwards /api to the local FastAPI service, so no host is hardcoded in pages.
 */
import type {
  CompaniesResponse,
  ConnectionChecks,
  SettingsField,
  SettingsStatus,
  CreateTaskResponse,
  HealthInfo,
  Report,
  ResearchRequest,
  TaskInfo,
} from '../types/api'
import { parseCreateTaskResponse, parseReport, parseTaskInfo, ValidationError } from './validate'

export type ApiErrorKind = 'network' | 'timeout' | 'http' | 'parse'

export class ApiError extends Error {
  kind: ApiErrorKind
  status?: number
  detail?: string

  constructor(kind: ApiErrorKind, message: string, status?: number, detail?: string) {
    super(message)
    this.name = 'ApiError'
    this.kind = kind
    this.status = status
    this.detail = detail
  }
}

const DEFAULT_TIMEOUT_MS = 15_000

function describeHttpError(status: number, body: unknown): string {
  if (status === 404) return 'Not found on the backend (HTTP 404).'
  if (status === 422) {
    const detail = (body as { detail?: unknown })?.detail
    if (Array.isArray(detail)) {
      const parts = detail
        .map((d) => {
          const loc = Array.isArray((d as { loc?: unknown[] }).loc)
            ? (d as { loc: unknown[] }).loc.join('.')
          : ''
          const msg = (d as { msg?: string }).msg ?? 'invalid value'
          return loc ? `${loc}: ${msg}` : msg
        })
        .join('; ')
      return `Invalid request (HTTP 422): ${parts}`
    }
    return 'Invalid request (HTTP 422).'
  }
  return `Backend request failed (HTTP ${status}).`
}

async function request(path: string, init?: RequestInit, timeoutMs = DEFAULT_TIMEOUT_MS): Promise<unknown> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  let response: Response
  try {
    response = await fetch(path, {
      ...init,
      signal: controller.signal,
      headers: { Accept: 'application/json', ...(init?.headers ?? {}) },
    })
  } catch (err) {
    clearTimeout(timer)
    if (err instanceof DOMException && err.name === 'AbortError') {
      throw new ApiError('timeout', 'The backend did not respond in time (request timeout).')
    }
    throw new ApiError(
      'network',
      'Cannot reach the backend. Make sure the FastAPI service is running.',
    )
  }
  clearTimeout(timer)

  let body: unknown = null
  const text = await response.text()
  if (text.length > 0) {
    try {
      body = JSON.parse(text)
    } catch {
      if (!response.ok) {
        throw new ApiError('http', describeHttpError(response.status, null), response.status)
      }
      throw new ApiError('parse', 'Backend returned a non-JSON response.', response.status)
    }
  }

  if (!response.ok) {
    const detail =
      typeof (body as { detail?: unknown })?.detail === 'string'
        ? (body as { detail: string }).detail
        : undefined
    throw new ApiError('http', describeHttpError(response.status, body), response.status, detail)
  }
  return body
}

export const api = {
  async getHealth(): Promise<HealthInfo> {
    const body = (await request('/api/health', undefined, 5_000)) as HealthInfo
    if (typeof body?.status !== 'string' || typeof body?.mode !== 'string') {
      throw new ApiError('parse', 'Health response has an unexpected shape.')
    }
    return body
  },

  async getSettings(): Promise<SettingsStatus> {
    return (await request('/api/settings', { cache: 'no-store' })) as SettingsStatus
  },

  async saveSetting(field: SettingsField, value: string): Promise<SettingsStatus> {
    return (await request('/api/settings', {
      method: 'PUT',
      redirect: 'error',
      cache: 'no-store',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ [field]: value }),
    })) as SettingsStatus
  },

  async checkSettingsConnections(): Promise<ConnectionChecks> {
    const result = (await request('/api/settings/connections', {
      method: 'POST',
      cache: 'no-store',
    }, 30_000)) as { checks: ConnectionChecks }
    return result.checks
  },

  async getCompanies(): Promise<CompaniesResponse> {
    const body = (await request('/api/companies', undefined, 5_000)) as CompaniesResponse
    if (!Array.isArray(body?.companies)) {
      throw new ApiError('parse', 'Companies response has an unexpected shape.')
    }
    return body
  },

  async createResearch(payload: ResearchRequest): Promise<CreateTaskResponse> {
    const body = await request('/api/research', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    try {
      return parseCreateTaskResponse(body)
    } catch (err) {
      if (err instanceof ValidationError) {
        throw new ApiError('parse', `Task creation response failed validation: ${err.message}`)
      }
      throw err
    }
  },

  async getTask(taskId: string): Promise<TaskInfo> {
    const body = await request(`/api/research/${encodeURIComponent(taskId)}`)
    try {
      return parseTaskInfo(body)
    } catch (err) {
      if (err instanceof ValidationError) {
        throw new ApiError('parse', `Task response failed validation: ${err.message}`)
      }
      throw err
    }
  },

  async getReport(reportId: string): Promise<Report> {
    const body = await request(`/api/reports/${encodeURIComponent(reportId)}`)
    try {
      return parseReport(body)
    } catch (err) {
      if (err instanceof ValidationError) {
        throw new ApiError('parse', `Report response failed validation: ${err.message}`)
      }
      throw err
    }
  },
}
