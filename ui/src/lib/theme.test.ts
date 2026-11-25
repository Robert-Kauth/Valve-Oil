import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import { afterEach, expect, it, vi } from 'vitest'
import type { Store } from './store'

vi.mock('./store', async (original) => ({
  ...await original<typeof import('./store')>(),
  useStore: <T, S>(store: Store<T>, selector: (s: T) => S) => selector(store.get()),
}))

afterEach(() => vi.unstubAllGlobals())

async function setup(saved: string | null, dark: boolean, blocked = false) {
  vi.resetModules()
  const media = Object.assign(new EventTarget(), { matches: dark })
  const window = Object.assign(new EventTarget(), { matchMedia: vi.fn(() => media) })
  const storage = {
    getItem: vi.fn(() => { if (blocked) throw new Error('denied'); return saved }),
    setItem: vi.fn(() => { if (blocked) throw new Error('denied') }),
  }
  const colors = { dark: false, meta: '' }
  const document = {
    documentElement: { classList: { toggle: (_name: string, value: boolean) => { colors.dark = value } } },
    querySelector: () => ({ setAttribute: (_name: string, value: string) => { colors.meta = value } }),
  }
  vi.stubGlobal('window', window)
  vi.stubGlobal('document', document)
  vi.stubGlobal('localStorage', storage)
  const script = readFileSync(new URL('../../index.html', import.meta.url), 'utf8').match(/<script>([\s\S]*?)<\/script>/)![1]
  runInNewContext(script, { window, document, localStorage: storage })
  const beforePaint = { ...colors }
  const theme = await import('./theme')
  return {
    ...theme, colors, beforePaint, storage,
    system(value: boolean) { media.matches = value; media.dispatchEvent(new Event('change')) },
    otherTab(value: string | null, key: string | null = 'vo-theme') {
      window.dispatchEvent(Object.assign(new Event('storage'), { key, newValue: value }))
    },
  }
}

it.each([
  [null, false, 'system', 'light'],
  [null, true, 'system', 'dark'],
  ['system', false, 'system', 'light'],
  ['system', true, 'system', 'dark'],
  ['light', true, 'light', 'light'],
  ['dark', false, 'dark', 'dark'],
  ['invalid', true, 'system', 'dark'],
] as const)('resolves saved %s with system dark=%s consistently before first paint', async (saved, systemDark, choice, resolved) => {
  const app = await setup(saved, systemDark)
  expect(app.useThemePreference()).toBe(choice)
  expect(app.useTheme()[0]).toBe(resolved)
  const colors = { dark: resolved === 'dark', meta: resolved === 'dark' ? '#0b0f15' : '#ffffff' }
  expect(app.beforePaint).toEqual(colors)
  expect(app.colors).toEqual(colors)
  expect(app.storage.setItem).not.toHaveBeenCalled()
})

it('follows device changes only in system mode and resumes when selected again', async () => {
  const app = await setup(null, false)
  app.system(true)
  expect(app.useTheme()[0]).toBe('dark')
  expect(app.colors.meta).toBe('#0b0f15')
  app.setTheme('light')
  app.system(false)
  app.system(true)
  expect(app.useTheme()[0]).toBe('light')
  app.setTheme('system')
  expect(app.useThemePreference()).toBe('system')
  expect(app.useTheme()[0]).toBe('dark')
  expect(app.storage.setItem).toHaveBeenLastCalledWith('vo-theme', 'system')
})

it('the topbar overrides the resolved system theme with the opposite explicit choice', async () => {
  const app = await setup(null, true)
  app.useTheme()[1]()
  expect(app.useThemePreference()).toBe('light')
  expect(app.colors.dark).toBe(false)
  app.useTheme()[1]()
  expect(app.useThemePreference()).toBe('dark')
})

it('synchronizes other tabs and restores system mode when storage is removed or cleared', async () => {
  const app = await setup('light', true)
  app.otherTab('dark', 'unrelated')
  expect(app.useThemePreference()).toBe('light')
  app.otherTab('dark')
  expect(app.useTheme()[0]).toBe('dark')
  app.otherTab(null)
  expect(app.useThemePreference()).toBe('system')
  app.otherTab('light')
  app.otherTab(null, null)
  expect(app.useThemePreference()).toBe('system')
  expect(app.colors.dark).toBe(true)
  expect(app.storage.setItem).not.toHaveBeenCalled()
})

it('uses the system default with denied storage and retains a manual choice in memory', async () => {
  const app = await setup(null, false, true)
  expect(app.beforePaint.dark).toBe(false)
  expect(app.useThemePreference()).toBe('system')
  expect(() => app.setTheme('dark')).not.toThrow()
  app.system(false)
  expect(app.useTheme()[0]).toBe('dark')
})
