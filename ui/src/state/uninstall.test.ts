import { afterEach, beforeEach, expect, it, vi } from 'vitest'

function fakeStorage() {
  const values = new Map<string, string>()
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => void values.set(key, String(value)),
    removeItem: (key: string) => void values.delete(key),
  }
}

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
const job = (state: string, extra: Record<string, unknown> = {}) => ({
  id: 'job-1',
  kind: 'uninstall',
  state,
  started_at: null,
  finished_at: null,
  log: ['Restoring QIDI files'],
  message: 'Restoring QIDI files',
  run_id: null,
  ...extra,
})
const status = (jobBody: unknown) => ({ schema: 1, busy: false, job: jobBody, uninstall: { allowed: true, blocked_by: [] } })

/** A page that has started an uninstall, on a printer whose web server answers as `answer` says. */
async function pageUninstalling(answer: (url: string) => Response | Promise<Response>) {
  vi.resetModules()
  vi.stubGlobal('location', new URL('http://printer.test/valve-oil/'))
  vi.stubGlobal('localStorage', fakeStorage())
  vi.stubGlobal('sessionStorage', fakeStorage())
  vi.stubGlobal('document', { hidden: false, addEventListener: () => {} })
  const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    const url = String(input)
    if (url.endsWith('/uninstall')) return json({ job: job('running') }, 202)
    return answer(url)
  })
  vi.stubGlobal('fetch', fetchMock)
  const { valveOil } = await import('./valve-oil')
  const { parseVoStatus } = await import('../qidi/vo-status')
  const mod = await import('./uninstall')
  valveOil.set((s) => ({ ...s, status: parseVoStatus(status(null)) }))
  return { ...mod, valveOil, fetchMock }
}

beforeEach(() => vi.useFakeTimers())
afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

it('keeps waiting while the printer restarts its services, and says uninstalled once Valve Oil UI is gone', async () => {
  let server: 'restarting' | 'removed' = 'restarting'
  const page = await pageUninstalling((url) => {
    if (url.includes('/vo-status/')) return server === 'restarting' ? new Response('Bad Gateway', { status: 502 }) : new Response('<html>Fluidd</html>', { status: 200 })
    // version.json: still Valve Oil UI's, then QIDI's Fluidd's page through its fallback.
    return server === 'restarting' ? json({ version: '26.11.23.1' }) : new Response('<html>Fluidd</html>', { status: 200 })
  })
  await page.startUninstall(false)
  expect(page.uninstall.get().phase).toBe('running')
  expect(JSON.parse(String(page.fetchMock.mock.calls[0][1]?.body))).toEqual({ keep_system_optimizations: false })

  await page.checkUninstall()
  expect(page.uninstall.get().phase).toBe('running')

  server = 'removed'
  await page.checkUninstall()
  expect(page.uninstall.get().phase).toBe('done')
})

it('shows why an uninstall stopped, and goes back to the page when closed', async () => {
  const page = await pageUninstalling((url) => (url.includes('/vo-status/') ? json(status(job('failed', { message: 'Klipper is printing.' }))) : json({ version: 'x' })))
  await page.startUninstall(true)
  await page.checkUninstall()
  expect(page.uninstall.get().phase).toBe('failed')
  expect(page.uninstall.get().job?.message).toBe('Klipper is printing.')

  page.closeUninstall()
  expect(page.uninstallShown(page.valveOil.get(), page.uninstall.get())).toBe(false)
})

it('reads a page that no longer answers as Valve Oil UI as removed, and no answer as nothing', async () => {
  const { pageRemoved } = await pageUninstalling(() => json({}))
  const answering = (response: Response | Error) => vi.fn(async () => (response instanceof Error ? Promise.reject(response) : response)) as unknown as typeof fetch
  expect(await pageRemoved(answering(new Response('', { status: 404 })))).toBe(true)
  expect(await pageRemoved(answering(new Response('<html></html>', { status: 200 })))).toBe(true)
  expect(await pageRemoved(answering(json({ version: '26.11.23.1' })))).toBe(false)
  expect(await pageRemoved(answering(new Response('', { status: 502 })))).toBe(false)
  expect(await pageRemoved(answering(new TypeError('Failed to fetch')))).toBe(false)
})
