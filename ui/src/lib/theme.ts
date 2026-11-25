import { createStore, useStore } from './store'

export type Theme = 'light' | 'dark'
export type ThemePreference = Theme | 'system'

const KEY = 'vo-theme'
const system = typeof window !== 'undefined' ? window.matchMedia?.('(prefers-color-scheme: dark)') : undefined
const preference = (value: string | null): ThemePreference => value === 'light' || value === 'dark' ? value : 'system'
const resolve = (choice: ThemePreference): Theme => choice === 'system' ? system?.matches ? 'dark' : 'light' : choice
let initial: ThemePreference = 'system'
try {
  initial = preference(localStorage.getItem(KEY))
} catch {
  // Storage can be unavailable; follow the device until a choice is made on this page.
}
const theme = createStore({ preference: initial, resolved: resolve(initial) })

function apply(choice: ThemePreference) {
  const resolved = resolve(choice)
  document.documentElement.classList.toggle('dark', resolved === 'dark')
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', resolved === 'dark' ? '#0b0f15' : '#ffffff')
  theme.set({ preference: choice, resolved })
}

export function setTheme(choice: ThemePreference) {
  apply(choice)
  try {
    localStorage.setItem(KEY, choice)
  } catch {
    // Private browsing can refuse storage; the choice still holds for this page.
  }
}

if (typeof document !== 'undefined') apply(initial)
if (typeof window !== 'undefined') {
  const followSystem = () => {
    if (theme.get().preference === 'system') apply('system')
  }
  // Safari before 14 has only the older addListener.
  if (system?.addEventListener) system.addEventListener('change', followSystem)
  else system?.addListener?.(followSystem)
  window.addEventListener('storage', (e) => {
    if (e.key === KEY || e.key === null) apply(preference(e.newValue))
  })
}

export function useThemePreference(): ThemePreference {
  return useStore(theme, (s) => s.preference)
}

// Renderers need the resolved colors, not the saved system/light/dark preference.
export function useTheme(): [Theme, () => void] {
  const resolved = useStore(theme, (s) => s.resolved)
  return [resolved, () => setTheme(theme.get().resolved === 'dark' ? 'light' : 'dark')]
}
