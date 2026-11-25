import { expect, it, vi } from 'vitest'

const delayedRussian = vi.hoisted(() => {
  let release!: () => void
  const wait = new Promise<void>((resolve) => { release = resolve })
  return { wait, release: () => release() }
})

vi.mock('./locales/ru/app.json', async (importOriginal) => {
  await delayedRussian.wait
  return importOriginal()
})

import { initLanguage, languageStore, setLanguage } from './index'

it('keeps the latest language when an earlier translation finishes loading afterward', async () => {
  const values = new Map<string, string>()
  vi.stubGlobal('localStorage', {
    setItem: (key: string, value: string) => values.set(key, value),
    getItem: (key: string) => values.get(key) ?? null,
  })
  const document = { documentElement: { lang: 'en' } }
  vi.stubGlobal('document', document)

  const russian = setLanguage('ru')
  await setLanguage('es')
  delayedRussian.release()
  await russian

  expect(languageStore.get()).toEqual({ lang: 'es', choice: 'es' })
  expect(document.documentElement.lang).toBe('es')
  expect(values.get('vo-language')).toBe('es')
  vi.unstubAllGlobals()
})

it('restores English document metadata when the initial translation cannot load', async () => {
  vi.resetModules()
  vi.doMock('./locales/ru/app.json', () => { throw new Error('Locale chunk unavailable') })
  vi.stubGlobal('localStorage', { getItem: () => 'ru' })
  const document = { documentElement: { lang: 'ru' } }
  vi.stubGlobal('document', document)
  try {
    const fresh = await import('./index')
    await fresh.initLanguage()
    expect(fresh.languageStore.get()).toEqual({ lang: 'en', choice: 'ru' })
    expect(document.documentElement.lang).toBe('en')
  } finally {
    vi.doUnmock('./locales/ru/app.json')
    vi.unstubAllGlobals()
  }
})

it('sets the document language from the saved UI choice before the first render', async () => {
  vi.stubGlobal('localStorage', { getItem: () => 'ru' })
  vi.stubGlobal('navigator', { languages: ['en-US'], language: 'en-US' })
  const document = { documentElement: { lang: 'en' } }
  vi.stubGlobal('document', document)
  try {
    delayedRussian.release()
    await initLanguage()
    expect(languageStore.get()).toEqual({ lang: 'ru', choice: 'ru' })
    expect(document.documentElement.lang).toBe('ru')
  } finally {
    vi.unstubAllGlobals()
  }
})
