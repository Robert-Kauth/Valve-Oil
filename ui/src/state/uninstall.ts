// Uninstalling Valve Oil from this page. The status service starts the installed bundle's uninstall, which goes on
// by itself, removes the service and this page as it goes, and ends with QIDI's Fluidd at the printer's address.
//
// While it runs, the page asks the service how it's going. Once the service has stopped answering and the page's own
// version.json is gone (nginx then answers with Fluidd's page, or not found), Valve Oil UI has been removed, and the
// page says so and leads to Fluidd. A printer that doesn't answer at all is still restarting its services.
import { createStore, useStore } from '../lib/store'
import { errorText } from '../lib/browser'
import { parseVoStatus, parseVoStatusJob, VoStatusRequestError, type VoStatusBlocker, type VoStatusJob } from '../qidi/vo-status'
import { poll, statusRequest, valveOil, type ValveOilState } from './valve-oil'
import { t } from '../i18n'

export type UninstallPhase = 'running' | 'failed' | 'done'

export type UninstallState = {
  /** The uninstall this page started, kept across a reload of this tab until it ends or is closed. */
  jobId: string | null
  startedAt: number | null
  /** How it's going, once the page has asked; null before then. */
  phase: UninstallPhase | null
  /** The service's last word on it: its output and, after a failure, why. */
  job: VoStatusJob | null
  pending: boolean
  error: string
  /** Why the printer refused, when it said. */
  blockedBy: VoStatusBlocker[]
  /** The stopped uninstall whose screen was closed, which the status may still say is running for a moment. */
  closed: string | null
}

const STARTED_KEY = 'vo-uninstall.started'
// Before the uninstall's job shows in the status, or after it has gone with the service, the page keeps waiting this
// long for the service to say something before it gives up on an uninstall it can't see.
const UNSEEN_MS = 2 * 60 * 1000
export const UNINSTALL_POLL_MS = 3000

function loadStarted(): { jobId: string; startedAt: number } | null {
  try {
    const value: unknown = JSON.parse(sessionStorage.getItem(STARTED_KEY) ?? 'null')
    if (value && typeof value === 'object' && 'jobId' in value && 'startedAt' in value) {
      const { jobId, startedAt } = value as { jobId: unknown; startedAt: unknown }
      if (typeof jobId === 'string' && typeof startedAt === 'number') return { jobId, startedAt }
    }
  } catch {
    // Unreadable or refused: this tab didn't start one.
  }
  return null
}

function saveStarted(started: { jobId: string; startedAt: number } | null) {
  try {
    if (started) sessionStorage.setItem(STARTED_KEY, JSON.stringify(started))
    else sessionStorage.removeItem(STARTED_KEY)
  } catch {
    // Private browsing can refuse storage; this page still knows.
  }
}

const saved = loadStarted()
export const uninstall = createStore<UninstallState>({
  jobId: saved?.jobId ?? null,
  startedAt: saved?.startedAt ?? null,
  phase: saved ? 'running' : null,
  job: null,
  pending: false,
  error: '',
  blockedBy: [],
  closed: null,
})

const patch = (changes: Partial<UninstallState>) => uninstall.set((s) => ({ ...s, ...changes }))

export const uninstallDisabled = (s: ValveOilState, u: UninstallState) => s.status?.uninstall?.allowed !== true || u.pending || s.reconnectingSince != null

/** Starts the uninstall, keeping the OS optimizations or undoing them, as the installer asks over SSH. */
export async function startUninstall(keepSystemOptimizations: boolean) {
  if (uninstallDisabled(valveOil.get(), uninstall.get())) return
  patch({ pending: true, error: '', blockedBy: [] })
  try {
    const job = parseVoStatusJob(await statusRequest('/uninstall', 'POST', undefined, { keep_system_optimizations: keepSystemOptimizations }))
    if (!job) throw new Error(t('updates.status.invalid_response'))
    const started = { jobId: job.id, startedAt: Date.now() }
    saveStarted(started)
    patch({ ...started, phase: 'running', job })
  } catch (e) {
    patch({ error: errorText(e), blockedBy: e instanceof VoStatusRequestError && e.status === 409 ? e.blockedBy : [] })
  } finally {
    patch({ pending: false })
  }
}

export const clearUninstallError = () => patch({ error: '', blockedBy: [] })

/** Closes a failed uninstall's screen, back to the rest of the page. */
export function closeUninstall() {
  const u = uninstall.get()
  saveStarted(null)
  patch({ jobId: null, startedAt: null, phase: null, job: null, closed: u.job?.id ?? u.jobId ?? valveOil.get().status?.job?.id ?? null })
  poll()
}

/**
 * Whether Valve Oil UI is gone from the printer: its version.json, which nginx serves uncached, now answers with
 * something else (QIDI's Fluidd's page, through its fallback) or not found. No answer, or a server error, says nothing.
 */
export async function pageRemoved(fetcher: typeof fetch = fetch): Promise<boolean> {
  let response: Response
  try {
    response = await fetcher('./version.json', { cache: 'no-store' })
  } catch {
    return false
  }
  if (response.status === 404) return true
  if (!response.ok) return false
  try {
    const body: unknown = await response.json()
    return !(body && typeof body === 'object' && typeof (body as { version?: unknown }).version === 'string')
  } catch {
    return true
  }
}

/** Asks the printer how the uninstall is going, once. */
export async function checkUninstall(fetcher: typeof fetch = fetch) {
  const s = uninstall.get()
  let status = null
  try {
    status = parseVoStatus(await statusRequest('/status'))
  } catch {
    // The service has stopped, for now or for good.
  }
  if (status) {
    const job = status.job?.kind === 'uninstall' && (s.jobId == null || status.job.id === s.jobId) ? status.job : null
    if (job) {
      // The service outlives the uninstall only when the uninstall didn't remove it, as when it failed.
      const phase: UninstallPhase = job.state === 'running' ? 'running' : job.state === 'failed' ? 'failed' : 'done'
      patch({ job, phase })
      if (phase !== 'running') saveStarted(null)
      return
    }
    // Another job, or none: the uninstall this page started isn't the service's, after a while.
    if (s.startedAt == null || Date.now() - s.startedAt > UNSEEN_MS) closeUninstall()
    return
  }
  if (await pageRemoved(fetcher)) {
    saveStarted(null)
    patch({ phase: 'done' })
  }
}

/** Whether the page shows the uninstall: one this tab started, or one the printer says is running. */
export const uninstallShown = (s: ValveOilState, u: UninstallState) =>
  u.phase != null || (s.status?.job?.kind === 'uninstall' && s.status.job.state === 'running' && s.status.job.id !== u.closed)

export function useUninstall(): UninstallState {
  return useStore(uninstall, (s) => s)
}

export function useUninstallShown(): boolean {
  const u = useUninstall()
  return useStore(valveOil, (s) => uninstallShown(s, u))
}
