import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import type { Status } from './printer'

const UPDATING_KEY = 'vo-status.updating-since'

function fakeStorage(entries: Record<string, string> = {}) {
  const values = new Map(Object.entries(entries))
  return {
    values,
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => void values.set(key, String(value)),
    removeItem: (key: string) => void values.delete(key),
  }
}

function statusResponse(busy: boolean) {
  return new Response(
    JSON.stringify({
      schema: 1,
      updated_at: '2026-11-23T20:00:00Z',
      release: { installed: '26.11.23.1' },
      printer: { id: 'max4', name: 'QIDI Max 4', firmware: '01.01.07.01', target_firmware: '01.01.07.01' },
      auto_update: { enabled: true, scheduled: true },
      check: { at: '2026-11-23T20:00:00Z', result: 'ok', message: '', next_at: null },
      pending: [],
      boards: [],
      run: null,
      job: null,
      busy,
      printer_busy: false,
      update_now: { allowed: !busy, blocked_by: busy ? [{ code: 'run_in_progress', message: 'an update is running' }] : [] },
    }),
    { status: 200, headers: { 'Content-Type': 'application/json' } },
  )
}

function stubPage(fetchResult: Response | Error) {
  vi.resetModules()
  vi.stubGlobal('location', new URL('http://printer.test/valve-oil/'))
  vi.stubGlobal('localStorage', fakeStorage())
  vi.stubGlobal('sessionStorage', fakeStorage())
  vi.stubGlobal('document', { hidden: false, addEventListener: () => {} })
  vi.stubGlobal('fetch', vi.fn().mockImplementation(() => (fetchResult instanceof Error ? Promise.reject(fetchResult) : Promise.resolve(fetchResult))))
}

/**
 * A page that saw an update start, reloaded or left open while the status service stopped answering, with Klipper
 * as `klipper` describes it.
 */
async function pageDuringUpdate(klipper: { klippy: 'ready' | 'startup'; warnings?: readonly string[] }) {
  vi.resetModules()
  const session = fakeStorage({ [UPDATING_KEY]: String(Date.now() - 60_000) })
  vi.stubGlobal('location', new URL('http://printer.test/valve-oil/'))
  vi.stubGlobal('localStorage', fakeStorage())
  vi.stubGlobal('sessionStorage', session)
  vi.stubGlobal('document', { hidden: false, addEventListener: () => {} })
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
  const { printer } = await import('./printer')
  const { valveOil, startValveOilStatus, updateRunning, printerReportsUpdateRunning, KLIPPER_QUIET_MS } = await import('./valve-oil')
  const warnings = klipper.warnings?.map((id) => ({ id, level: 'error', title: id, message: '' }))
  const status: Status = warnings ? { vo_printer_warnings: { warnings } } : {}
  printer.set((s) => ({ ...s, conn: 'open', klippy: klipper.klippy, status }))
  startValveOilStatus()
  await vi.advanceTimersByTimeAsync(0)
  return { running: () => updateRunning(valveOil.get()), reported: () => printerReportsUpdateRunning(printer.get()), valveOil, session, quiet: KLIPPER_QUIET_MS }
}

beforeEach(() => vi.useFakeTimers())
afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

it('stops saying the printer is updating once Klipper has been ready a while without an update running', async () => {
  const page = await pageDuringUpdate({ klippy: 'ready', warnings: ['valve_oil_update_interrupted', 'valve_oil_release_waiting'] })
  expect(page.running()).toBe(true)

  // Update now's installer may not have recorded its run yet.
  await vi.advanceTimersByTimeAsync(page.quiet / 2)
  expect(page.running()).toBe(true)

  await vi.advanceTimersByTimeAsync(page.quiet)
  expect(page.running()).toBe(false)
  expect(page.valveOil.get().unavailable).toBe(true)
  // A reload doesn't bring it back.
  expect(page.session.values.has(UPDATING_KEY)).toBe(false)
})

it.each([
  ['Klipper says an update is running', { klippy: 'ready', warnings: ['valve_oil_update_running'] }],
  ['Klipper is restarting', { klippy: 'startup' }],
  ['Klipper has no Valve Oil warnings to say', { klippy: 'ready' }],
] as const)('keeps saying the printer is updating while %s', async (_, klipper) => {
  const page = await pageDuringUpdate(klipper)
  await vi.advanceTimersByTimeAsync(page.quiet * 5)
  expect(page.running()).toBe(true)
  expect(page.session.values.has(UPDATING_KEY)).toBe(true)
})

it('uses Klipper’s live warning before the status poll sees an automatic update', async () => {
  const page = await pageDuringUpdate({ klippy: 'ready', warnings: ['valve_oil_update_running'] })
  expect(page.reported()).toBe(true)
})

it.each<[string, Response | Error, 'running' | 'idle' | 'unknown']>([
  ['running', statusResponse(true), 'running'],
  ['idle', statusResponse(false), 'idle'],
  ['network failure', new TypeError('Failed to fetch'), 'unknown'],
  ['HTTP failure', new Response(JSON.stringify({ error: 'unavailable' }), { status: 503, headers: { 'Content-Type': 'application/json' } }), 'unknown'],
  ['timeout', new DOMException('The operation timed out', 'TimeoutError'), 'unknown'],
  ['malformed payload', new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } }), 'unknown'],
])('reports a fresh %s state for a consequential action', async (_, response, expected) => {
  stubPage(response)
  const { readLiveUpdateState, updateRunningNow } = await import('./valve-oil')
  expect(updateRunningNow()).toBe(false)
  expect(await readLiveUpdateState()).toBe(expected)
  expect(updateRunningNow()).toBe(expected === 'running')
})

it('bounds the live status read instead of waiting forever', async () => {
  stubPage(statusResponse(false))
  const { readLiveUpdateState } = await import('./valve-oil')
  expect(await readLiveUpdateState()).toBe('idle')
  const signal = vi.mocked(fetch).mock.calls[0]?.[1]?.signal
  expect(signal).toBeInstanceOf(AbortSignal)
})

it('does not authorize from a stale cached idle state when the live read fails', async () => {
  stubPage(statusResponse(false))
  const { readLiveUpdateState } = await import('./valve-oil')
  expect(await readLiveUpdateState()).toBe('idle')
  vi.mocked(fetch).mockRejectedValueOnce(new TypeError('Failed to fetch'))
  expect(await readLiveUpdateState()).toBe('unknown')
})

it('does not let a delayed idle response overwrite a newly observed update', async () => {
  vi.resetModules()
  vi.stubGlobal('location', new URL('http://printer.test/valve-oil/'))
  vi.stubGlobal('localStorage', fakeStorage())
  vi.stubGlobal('sessionStorage', fakeStorage())
  vi.stubGlobal('document', { hidden: false, addEventListener: () => {} })
  let answer!: (response: Response) => void
  vi.stubGlobal('fetch', vi.fn().mockImplementation(() => new Promise<Response>((resolve) => (answer = resolve))))

  const { readLiveUpdateState, updateRunningNow, valveOil } = await import('./valve-oil')
  const check = readLiveUpdateState()
  expect(fetch).toHaveBeenCalledOnce()
  valveOil.set((s) => ({ ...s, expectUpdate: true, reconnectingSince: Date.now() }))
  answer(statusResponse(false))

  expect(await check).toBe('running')
  expect(updateRunningNow()).toBe(true)
})
