import { resolveLanguage, savedChoice } from './preference'

// Runs inline while parsing HTML, before Chromium can classify the empty app shell.
document.documentElement.lang = resolveLanguage(savedChoice())
