/**
 * Client-side record of research tasks the user started in this browser.
 * This is navigation convenience only — never treated as report data.
 */
import type { RecentTaskEntry } from '../types/api'

const KEY = 'seldon.recentTasks'
const MAX_ENTRIES = 20

export function loadRecentTasks(): RecentTaskEntry[] {
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return []
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed.filter(
      (e): e is RecentTaskEntry =>
        typeof e === 'object' && e !== null && typeof (e as RecentTaskEntry).task_id === 'string',
    )
  } catch {
    return []
  }
}

function save(entries: RecentTaskEntry[]): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(entries.slice(0, MAX_ENTRIES)))
  } catch {
    // Storage unavailable (private mode etc.): recents are optional.
  }
}

export function upsertRecentTask(entry: RecentTaskEntry): void {
  const rest = loadRecentTasks().filter((e) => e.task_id !== entry.task_id)
  save([entry, ...rest])
}

/** Insert only when the task is not recorded yet; otherwise just refresh status/report. */
export function ensureRecentTask(entry: RecentTaskEntry): void {
  const existing = loadRecentTasks()
  if (!existing.some((e) => e.task_id === entry.task_id)) {
    save([entry, ...existing])
  } else {
    save(existing.map((item) => item.task_id !== entry.task_id ? item : {
      ...item,
      query: entry.query && entry.query !== '(recovered task)' ? entry.query : item.query,
      status: entry.status,
      report_id: entry.report_id ?? item.report_id,
    }))
  }
}

export function updateRecentTaskStatus(taskId: string, status: RecentTaskEntry['status'], reportId?: string | null): void {
  const entries = loadRecentTasks()
  const idx = entries.findIndex((e) => e.task_id === taskId)
  if (idx >= 0) {
    entries[idx] = {
      ...entries[idx],
      status,
      report_id: reportId ?? entries[idx].report_id,
    }
    save(entries)
  }
}

export function removeRecentTask(taskId: string): void {
  save(loadRecentTasks().filter((e) => e.task_id !== taskId))
}
