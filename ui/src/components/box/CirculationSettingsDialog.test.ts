import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest'
import type { PrinterState } from '../../state/printer'
import type { BoxState, Lists } from '../../state/box-model'

const NO_LISTS: Lists = { legacy: null, database: null, databaseError: false }

const printerState = (boxes: Record<number, Record<string, unknown>>): PrinterState => ({
  conn: 'open',
  klippy: 'ready',
  klippyMessage: '',
  objects: [],
  status: {
    multi_color_controller: {},
    save_variables: { variables: { box_count: 2, enable_box: 1 } },
    print_stats: { state: 'standby' },
    vo_box_circulation: { boxes },
  },
  temps: {},
  console: [],
  commands: [],
  heaterSettings: {},
  settings: {},
  server: null,
  host: null,
  filesRev: 0,
  historyRev: 0,
})

let deriveBox: (state: PrinterState, lists: Lists) => BoxState
let CirculationSettingsDialog: typeof import('./CirculationSettingsDialog').CirculationSettingsDialog
let setPrinter: (state: PrinterState) => void
let refreshBoxCirculation: typeof import('../../state/box').refreshBoxCirculation
let printerClient: typeof import('../../state/printer').client
let getPrinter: () => PrinterState

beforeAll(async () => {
  vi.stubGlobal('location', new URL('http://printer.test/valve-oil/'))
  vi.stubGlobal('localStorage', { getItem: () => null, setItem: () => {} })
  vi.stubGlobal('sessionStorage', { getItem: () => null, setItem: () => {} })
  vi.stubGlobal('HTMLElement', class HTMLElement {})
  vi.stubGlobal('document', { activeElement: null, documentElement: { lang: 'en' } })
  const i18n = await import('../../i18n')
  await i18n.setLanguage('ru')
  deriveBox = (await import('../../state/box-model')).deriveBox
  const printer = await import('../../state/printer')
  setPrinter = (state) => printer.printer.set(state)
  getPrinter = printer.printer.get
  printerClient = printer.client
  refreshBoxCirculation = (await import('../../state/box')).refreshBoxCirculation
  CirculationSettingsDialog = (await import('./CirculationSettingsDialog')).CirculationSettingsDialog
})

afterAll(() => vi.unstubAllGlobals())

const renderSettings = (box: BoxState, boxNumber: number) => renderToStaticMarkup(createElement(CirculationSettingsDialog, { box, boxNumber, onClose: () => {} }))

describe('CirculationSettingsDialog', () => {
  it('offers safe defaults without exposing an invalid saved-settings reason code', () => {
    const state = printerState({
      1: { threshold: null, hysteresis: 3, reason: 'off', settings_error: 'invalid_saved_settings', configurable: true },
      2: { threshold: 35, hysteresis: 0.5, reason: 'off', configurable: true },
    })
    setPrinter(state)
    const box = deriveBox(state, NO_LISTS)

    const html = renderSettings(box, 1)
    expect(html).toContain('Сохранённые настройки циркуляции некорректны')
    expect(html).not.toContain('invalid_saved_settings')
    expect(html).toContain('value="35"')
    expect(html).toContain('value="0.5"')
    expect(html).not.toContain('disabled=""')
  })

  it('blocks only the selected faulted Box', () => {
    const state = printerState({
      1: { threshold: 35, hysteresis: 0.5, reason: 'off', configurable: true },
      2: { threshold: 35, hysteresis: 0.5, reason: 'sensor_fault', sensor_fault: 'adc_timeout', configurable: true },
    })
    setPrinter(state)
    const box = deriveBox(state, NO_LISTS)

    const healthy = renderSettings(box, 1)
    expect(healthy).not.toContain('Сначала устраните неисправность датчика этого Box')
    expect(healthy).not.toContain('disabled=""')

    const faulted = renderSettings(box, 2)
    expect(faulted).toContain('Сначала устраните неисправность датчика этого Box')
    expect(faulted).not.toContain('adc_timeout')
    expect(faulted).toContain('disabled=""')
  })

  it('clears settings_error when the saved status is refreshed', async () => {
    const state = printerState({
      1: { threshold: null, hysteresis: 3, reason: 'off', settings_error: 'invalid_saved_settings', configurable: true },
    })
    setPrinter(state)
    const call = vi.spyOn(printerClient, 'call').mockResolvedValue({ status: { vo_box_circulation: { boxes: {
      1: { threshold: 36, hysteresis: 0.6, reason: 'off', settings_error: null, configurable: true },
    } } } })

    await expect(refreshBoxCirculation(1, 36, 0.6)).resolves.toBe(true)
    expect(getPrinter().status.vo_box_circulation?.boxes?.[1]?.settings_error).toBeNull()
    call.mockRestore()
  })
})
