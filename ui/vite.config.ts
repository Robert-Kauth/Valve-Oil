/// <reference types="vitest/config" />
import { build, defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { appendFileSync, readFileSync, statSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { gzipSync } from 'node:zlib'
import { fileURLToPath } from 'node:url'

const pkg = JSON.parse(readFileSync(new URL('./package.json', import.meta.url), 'utf8'))
// The installer bundle passes the package version; local builds use package.json's.
const appVersion: string = process.env.VO_UI_VERSION || pkg.version
const statusTarget = process.env.VO_PRINTER || 'http://localhost:7125'

// The document language script, built once and again only when its sources change, not on every page load.
const languageSources = ['./src/i18n/document-language.ts', './src/i18n/preference.ts'].map((path) => fileURLToPath(new URL(path, import.meta.url)))
let languageScript: { key: string; code: Promise<string> } | null = null
function documentLanguageScript(): Promise<string> {
  const key = languageSources.map((path) => statSync(path).mtimeMs).join(':')
  if (languageScript?.key === key) return languageScript.code
  const code = buildDocumentLanguageScript()
  languageScript = { key, code }
  // A failed build is tried again on the next request instead of being kept.
  code.catch(() => languageScript?.code === code && (languageScript = null))
  return code
}

async function buildDocumentLanguageScript(): Promise<string> {
  const result = await build({
    configFile: false,
    logLevel: 'silent',
    build: {
      write: false,
      target: 'es2015',
      rolldownOptions: {
        input: languageSources[0],
        output: { format: 'iife' },
      },
    },
  })
  const bundles = Array.isArray(result) ? result : [result]
  const script = bundles.flatMap((bundle) => 'output' in bundle ? bundle.output : [])
    .find((file) => file.type === 'chunk' && file.isEntry)
  if (!script || script.type !== 'chunk') throw new Error('Document language script was not built')
  return script.code
}

// Relative base so the build works from any sub-path the web server mounts it on.
export default defineConfig({
  base: './',
  define: {
    __APP_VERSION__: JSON.stringify(appVersion),
  },
  plugins: [
    {
      // A deferred module is too late: browsers can classify the empty shell before locales load.
      name: 'valve-oil-document-language',
      transformIndexHtml: {
        order: 'pre',
        async handler() {
          return [{ tag: 'script', attrs: { id: 'vo-document-language' }, children: await documentLanguageScript(), injectTo: 'head' }]
        },
      },
    },
    react(),
    tailwindcss(),
    {
      // version.json lets open tabs notice a newly installed build (src/lib/update-check.ts).
      name: 'valve-oil-version',
      generateBundle() {
        this.emitFile({ type: 'asset', fileName: 'version.json', source: JSON.stringify({ version: appVersion }) + '\n' })
      },
    },
    {
      // Vite lists the licenses of bundled scripts only. Montserrat arrives through CSS, and the SIL Open Font License
      // asks for its text to go with the font, so it's added to licenses.md here.
      name: 'valve-oil-font-license',
      apply: 'build',
      writeBundle(options) {
        const fontPkg = JSON.parse(readFileSync(new URL('./node_modules/@fontsource-variable/montserrat/package.json', import.meta.url), 'utf8'))
        const text = readFileSync(new URL('./node_modules/@fontsource-variable/montserrat/LICENSE', import.meta.url), 'utf8')
        appendFileSync(join(options.dir!, 'licenses.md'), `\n## ${fontPkg.name} - ${fontPkg.version} (${fontPkg.license})\n\nThe Montserrat typeface.\n\n\`\`\`\n${text.trim()}\n\`\`\`\n`)
      },
    },
    {
      // The printer's nginx has gzip_static on: ship a .gz beside each script and stylesheet so the RK3308 doesn't
      // compress them on every request. gzipSync writes no timestamp, so the archive stays reproducible.
      name: 'valve-oil-gzip',
      apply: 'build',
      writeBundle(options, bundle) {
        for (const file of Object.keys(bundle)) {
          if (!/\.(js|css|svg)$/.test(file)) continue
          const path = join(options.dir!, file)
          writeFileSync(`${path}.gz`, gzipSync(readFileSync(path), { level: 9 }))
        }
      },
    },
  ],
  server: {
    proxy: {
      // Valve Oil's status service, which the printer's nginx serves on the same origin as Valve Oil UI. VO_PRINTER
      // names the printer or fake Moonraker to send it to; Origin is rewritten to match, as the service refuses
      // cross-origin changes.
      '/vo-status': {
        target: statusTarget,
        changeOrigin: true,
        configure: (proxy) => proxy.on('proxyReq', (req) => req.getHeader('origin') && req.setHeader('origin', new URL(statusTarget).origin)),
      },
      // The touchscreen stream (vo-remote-screen), a websocket on the same origin.
      '/vo-screen': { target: statusTarget, ws: true, changeOrigin: true },
      // Valve Oil's add-ons service, on the same origin and with the same cross-origin rule.
      '/vo-addons': {
        target: statusTarget,
        changeOrigin: true,
        configure: (proxy) => proxy.on('proxyReq', (req) => req.getHeader('origin') && req.setHeader('origin', new URL(statusTarget).origin)),
      },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    sourcemap: false,
    chunkSizeWarningLimit: 800,
    // Licenses of the bundled npm packages (React, lucide), shipped with the build.
    license: { fileName: 'licenses.md' },
  },
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
  },
})
