// Shared by the parser-time document script and the application's language selector.
export const LANGUAGES = [
  { code: 'en', name: 'English' },
  { code: 'es', name: 'Español' },
  { code: 'ru', name: 'Русский' },
] as const
export type Lang = (typeof LANGUAGES)[number]['code']
/** A language picked in Settings, or `auto` to follow the browser. */
export type LangChoice = Lang | 'auto'

export const STORAGE_KEY = 'vo-language'
const isLang = (v: unknown): v is Lang => LANGUAGES.some((l) => l.code === v)

export function savedChoice(): LangChoice {
  try {
    const v = localStorage.getItem(STORAGE_KEY)
    return isLang(v) ? v : 'auto'
  } catch {
    return 'auto'
  }
}

/** The first of the browser's languages that the UI has, else English. */
export function browserLanguage(): Lang {
  const list = typeof navigator === 'undefined' ? [] : navigator.languages?.length ? navigator.languages : [navigator.language]
  for (const tag of list) {
    const primary = tag?.toLowerCase().split('-')[0]
    if (isLang(primary)) return primary
  }
  return 'en'
}

export const resolveLanguage = (choice: LangChoice): Lang => choice === 'auto' ? browserLanguage() : choice
