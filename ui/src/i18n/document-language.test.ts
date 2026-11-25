import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { runInNewContext } from 'node:vm'
import { beforeAll, expect, it } from 'vitest'
import { createServer } from 'vite'

let script: string
beforeAll(async () => {
  const server = await createServer({
    configFile: fileURLToPath(new URL('../../vite.config.ts', import.meta.url)),
    server: { middlewareMode: true, watch: null },
  })
  try {
    const html = await server.transformIndexHtml('/', readFileSync(new URL('../../index.html', import.meta.url), 'utf8'))
    const head = html.match(/<head>([\s\S]*?)<\/head>/)?.[1] ?? ''
    const inline = head.match(/<script\s+id="vo-document-language">([\s\S]*?)<\/script>/)
    expect(inline, 'Language must be available from a classic inline head script, without fetching application modules').not.toBeNull()
    script = inline![1]
  } finally {
    await server.close()
  }
})

it.each([
  { saved: 'ru', languages: ['en-US'], expected: 'ru' },
  { saved: 'es', languages: ['ru-RU'], expected: 'es' },
  { saved: 'en', languages: ['ru-RU'], expected: 'en' },
  { saved: null, languages: ['ru-RU', 'en'], expected: 'ru' },
  { saved: 'auto', languages: ['de-DE', 'es-MX', 'ru'], expected: 'es' },
  { saved: 'invalid', languages: ['RU-ru'], expected: 'ru' },
  { saved: null, languages: ['de-DE'], expected: 'en' },
  { saved: null, languages: [], expected: 'ru' },
])('declares $expected before any app or locale request ($saved, $languages)', ({ saved, languages, expected }) => {
  const document = { documentElement: { lang: 'en' } }
  runInNewContext(script, {
    document,
    navigator: { languages, language: 'ru-RU' },
    localStorage: { getItem: () => saved },
  })
  expect(document.documentElement.lang).toBe(expected)
})

it('still declares the browser language when storage access is denied', () => {
  const document = { documentElement: { lang: 'en' } }
  runInNewContext(script, {
    document,
    navigator: { languages: ['ru-RU'] },
    get localStorage() { throw new Error('Storage access denied') },
  })
  expect(document.documentElement.lang).toBe('ru')
})
