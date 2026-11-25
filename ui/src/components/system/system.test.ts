// The System screen's calculations, one block per source file.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createBuffer, klipperChartValues, mcuChartValues, pushSample } from './chart-data'
import { collectorPath, referencedObjects } from './collectors'
import { MAX_LINES, tailText } from './log-tail'
import { faultReportName, parseFaultCheck } from './fault-check'

function fakeStorage() {
  const values = new Map<string, string>()
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => void values.set(key, value),
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

type LiveCase = 'idle' | 'running' | 'network failure' | 'HTTP failure' | 'timeout' | 'malformed payload' | 'stale cache'
type ActionCase = 'klipper service start' | 'klipper service stop' | 'klipper service restart' | 'moonraker service start' | 'moonraker service restart' | 'Klipper restart' | 'firmware restart' | 'Klipper service recovery' | 'host reboot' | 'host shutdown'

async function actionHarness(state: LiveCase) {
  vi.resetModules()
  vi.stubGlobal('location', new URL('http://printer.test/valve-oil/'))
  vi.stubGlobal('localStorage', fakeStorage())
  vi.stubGlobal('sessionStorage', fakeStorage())
  vi.stubGlobal('document', { hidden: false, addEventListener: () => {} })
  const response = state === 'idle' || state === 'stale cache' ? statusResponse(false)
    : state === 'running' ? statusResponse(true)
      : state === 'malformed payload' ? new Response('{}', { status: 200 })
        : state === 'HTTP failure' ? new Response(JSON.stringify({ error: 'unavailable' }), { status: 503, headers: { 'Content-Type': 'application/json' } })
          : state === 'timeout' ? new DOMException('The operation timed out', 'TimeoutError')
            : new TypeError('Failed to fetch')
  const fetchMock = vi.fn().mockImplementation(() => (response instanceof Error ? Promise.reject(response) : Promise.resolve(response)))
  vi.stubGlobal('fetch', fetchMock)
  const actions = await import('./Services')
  const { client } = await import('../../state/printer')
  if (state === 'stale cache') {
    const { readLiveUpdateState } = await import('../../state/valve-oil')
    expect(await readLiveUpdateState()).toBe('idle')
    fetchMock.mockRejectedValueOnce(new TypeError('Failed to fetch'))
  }
  const call = vi.spyOn(client, 'call').mockResolvedValue(undefined)
  return { actions, call }
}

async function runAction(action: ActionCase, state: LiveCase, goAheadUnchecked = false) {
  const { actions, call } = await actionHarness(state)
  const unchecked = vi.fn().mockResolvedValue(goAheadUnchecked)
  if (action === 'klipper service start') await actions.dispatchServiceAction('klipper', 'start', 'idle', unchecked)
  else if (action === 'klipper service stop') await actions.dispatchServiceAction('klipper', 'stop', 'idle', unchecked)
  else if (action === 'klipper service restart') await actions.dispatchServiceAction('klipper', 'restart', 'idle', unchecked)
  else if (action === 'moonraker service start') await actions.dispatchServiceAction('moonraker', 'start', 'idle', unchecked)
  else if (action === 'moonraker service restart') await actions.dispatchServiceAction('moonraker', 'restart', 'idle', unchecked)
  else if (action === 'Klipper restart') await actions.dispatchKlipperAction('restart', 'idle', unchecked)
  else if (action === 'firmware restart') await actions.dispatchKlipperAction('firmware-restart', 'idle', unchecked)
  else if (action === 'Klipper service recovery') await actions.dispatchKlipperAction('service-restart', 'idle', unchecked)
  else if (action === 'host reboot') await actions.dispatchHostPowerAction('reboot', 'idle', unchecked)
  else await actions.dispatchHostPowerAction('shutdown', 'idle', unchecked)
  const expectedMethod = action === 'Klipper restart' ? 'printer.restart'
    : action === 'firmware restart' ? 'printer.firmware_restart'
      : action === 'host reboot' ? 'machine.reboot'
        : action === 'host shutdown' ? 'machine.shutdown'
          : action === 'klipper service start' || action === 'moonraker service start' ? 'machine.services.start'
            : action === 'klipper service stop' ? 'machine.services.stop'
              : 'machine.services.restart'
  const expectedService = action === 'moonraker service start' || action === 'moonraker service restart' ? 'moonraker'
    : action === 'klipper service start' || action === 'klipper service stop' || action === 'klipper service restart' || action === 'Klipper service recovery' ? 'klipper'
      : null
  return { sent: call.mock.calls.filter(([method, params]) => method === expectedMethod && (expectedService == null || params?.service === expectedService)), asked: unchecked.mock.calls.length }
}

afterEach(() => vi.unstubAllGlobals())

describe('consequential actions', () => {
  const actions: ActionCase[] = ['klipper service start', 'klipper service stop', 'klipper service restart', 'moonraker service start', 'moonraker service restart', 'Klipper restart', 'firmware restart', 'Klipper service recovery', 'host reboot', 'host shutdown']

  const unknownStates: LiveCase[] = ['network failure', 'HTTP failure', 'timeout', 'malformed payload', 'stale cache']

  it.each(actions)('dispatches %s once after a fresh idle read, without asking', async (action) => {
    expect(await runAction(action, 'idle')).toEqual({ sent: [expect.anything()], asked: 0 })
  })

  it.each(actions)('does not dispatch %s, or offer to, while an update runs', async (action) => {
    expect(await runAction(action, 'running', true)).toEqual({ sent: [], asked: 0 })
  })

  it.each(actions.flatMap((action) => unknownStates.map((state) => [action, state] as const)))('asks before %s when the live state is %s, and holds it when declined', async (action, state) => {
    expect(await runAction(action, state, false)).toEqual({ sent: [], asked: 1 })
  })

  // Restarting Klipper or rebooting is how an operator recovers a status service that doesn't answer.
  it.each(actions.flatMap((action) => unknownStates.map((state) => [action, state] as const)))('dispatches %s once when the operator goes ahead with the live state %s', async (action, state) => {
    expect(await runAction(action, state, true)).toEqual({ sent: [expect.anything()], asked: 1 })
  })
})

describe('chart-data.ts', () => {
  describe('mcuChartValues', () => {
    it('charts load and awake time as Fluidd does', () => {
      expect(mcuChartValues({ mcu_awake: 0.039, mcu_task_avg: 0.000026, mcu_task_stddev: 0.000018 })).toEqual({ load: 3.2, awake: 0.78 })
    })
    it('charts only what an MCU reports', () => {
      expect(mcuChartValues({ mcu_awake: 0.012 })).toEqual({ awake: 0.24 })
      expect(mcuChartValues(undefined)).toEqual({})
    })
  })

  describe('klipperChartValues', () => {
    it('turns cputime into a percent of a core over the sample time', () => {
      expect(klipperChartValues({ sysload: 0.65, cputime: 101.5 }, 100, 2, { timers: 80, main_thread_load: 0.251 })).toEqual({ load: 0.65, cputime_change: 75, timers: 80, main_thread: 25.1 })
    })
    it('waits for a second sample before charting Klipper load', () => {
      expect(klipperChartValues({ sysload: 0.5, cputime: 10 }, null, 0)).toEqual({ load: 0.5 })
    })
  })

  describe('pushSample', () => {
    it('keeps columns aligned and drops the oldest past retention', () => {
      const b = createBuffer()
      pushSample(b, 1, { a: 1 }, 3)
      pushSample(b, 2, { a: 2, b: 5 }, 3)
      pushSample(b, 3, { b: 6 }, 3)
      pushSample(b, 4, { a: 4, b: 7 }, 3)
      expect(b.times).toEqual([2, 3, 4])
      expect(b.cols.a).toEqual([2, NaN, 4])
      expect(b.cols.b).toEqual([5, 6, 7])
    })
  })
})

describe('collectors.ts', () => {
  describe('referencedObjects', () => {
    it('finds dotted and bracketed objects', () => {
      expect(referencedObjects("printer.motion_report.live_extruder_velocity * Math.PI * (printer.configfile.settings.extruder.filament_diameter / 2) ** 2 + printer['tmc2209 extruder'].run_current")).toEqual([
        'motion_report',
        'configfile',
        'tmc2209 extruder',
      ])
    })
    it('finds nothing in a constant', () => {
      expect(referencedObjects('12')).toEqual([])
    })
  })

  describe('collectorPath', () => {
    it('builds paths a collector can evaluate', () => {
      expect(collectorPath(['toolhead', 'position', 0])).toBe('printer.toolhead.position[0]')
      expect(collectorPath(['mcu THR', 'last_stats', 'mcu_awake'])).toBe("printer['mcu THR'].last_stats.mcu_awake")
      expect(collectorPath(['gcode_macro _X', "it's"])).toBe("printer['gcode_macro _X']['it\\'s']")
    })
  })
})

describe('log-tail.ts', () => {
  describe('tailText', () => {
    it('keeps a whole log whole', () => {
      expect(tailText('a\nb\n', 0, 4)).toEqual({ lines: ['a', 'b'], shown: 4, total: 4, truncated: false })
    })
    it('drops the partial first line of a tail', () => {
      expect(tailText('ial line\nnext\nlast\n', 100, 119)).toEqual({ lines: ['next', 'last'], shown: 19, total: 119, truncated: true })
    })
    it('caps very long logs to the newest lines', () => {
      const t = tailText(Array.from({ length: MAX_LINES + 5 }, (_, i) => `l${i}`).join('\n'), 0, 1)
      expect(t.lines.length).toBe(MAX_LINES)
      expect(t.lines[0]).toBe('l5')
      expect(t.truncated).toBe(true)
    })
  })
})

describe('fault-check.ts', () => {
  // A finished check as installer/system/vo_status.py sends it.
  const finding = (over: Record<string, unknown> = {}) => ({
    id: 'box_step_clock_offset',
    title: "A Box slot motor's step clock is off Klipper's",
    severity: 'fixed',
    meaning: 'The Box kept a slot motor’s step clock seconds away from Klipper’s.',
    action: 'Nothing, on Valve Oil 26.11.22.2 or later.',
    fixed_in: '26.11.22.2',
    docs: 'https://github.com/thelegendtubaguy/valve-oil/blob/main/docs/troubleshooting.md#box-slot-step-clock',
    count: 2,
    first: '2026-11-22 09:07:58',
    last: '2026-11-22 11:40:02',
    details: ["Box 1 slot stepper oid 11: 23.7 s behind Klipper's"],
    evidence: ["2026-11-22 09:07:58 - mcu 'mcu_box1': got {'oid': 11, 'clk': 4035537278, '#name': 'last_step_clk'}"],
    ...over,
  })
  const done = (findings: unknown[], checked: Record<string, unknown> = {}) => ({
    state: 'done',
    since: 'all',
    started_at: '2026-11-22T12:00:00Z',
    finished_at: '2026-11-22T12:00:09Z',
    error: null,
    result: {
      schema: 1,
      checked: { since: 'all', files: [{ name: 'klippy.log', size: 2048, read_from: 0, bytes_read: 2048 }], from: '2026-11-20 08:00:00', to: '2026-11-22 11:59:59', complete: true, stopped: null, elapsed: 4.2, ...checked },
      installed: '26.11.22.2',
      findings,
    },
    report: 'Valve Oil diagnostics\n',
  })

  describe('parseFaultCheck', () => {
    it('reads a finished check', () => {
      const check = parseFaultCheck(done([finding()]))
      expect(check?.state).toBe('done')
      expect(check?.report).toBe('Valve Oil diagnostics\n')
      expect(check?.result?.checked).toMatchObject({ from: '2026-11-20 08:00:00', to: '2026-11-22 11:59:59', complete: true, stopped: null })
      expect(check?.result?.findings).toEqual([finding()])
    })

    it('reads no check, a running one, and a failed one', () => {
      expect(parseFaultCheck({ state: 'idle', since: null, started_at: null, finished_at: null, error: null, result: null, report: null })?.state).toBe('idle')
      expect(parseFaultCheck({ state: 'running', since: 'day', started_at: '2026-11-22T12:00:00Z' })).toMatchObject({ state: 'running', since: 'day', result: null })
      expect(parseFaultCheck({ state: 'failed', error: 'The check could not be run.' })).toMatchObject({ state: 'failed', error: 'The check could not be run.', result: null })
    })

    it('is no check for a body it does not understand', () => {
      for (const body of [null, 'done', [], {}, { state: 'paused' }]) expect(parseFaultCheck(body)).toBeNull()
    })

    it('reads no result from another schema or a malformed one', () => {
      expect(parseFaultCheck({ ...done([finding()]), result: { ...done([]).result, schema: 2 } })?.result).toBeNull()
      expect(parseFaultCheck({ ...done([]), result: 'nothing' })?.result).toBeNull()
    })

    it('leaves out findings without an id or a title', () => {
      const check = parseFaultCheck(done([finding({ id: undefined }), finding({ id: 'qidi_error', title: '' }), 'qidi_error', null, finding({ id: 'kept' })]))
      expect(check?.result?.findings.map((f) => f.id)).toEqual(['kept'])
    })

    it('reads an unknown severity as info and lists the most severe first', () => {
      const check = parseFaultCheck(done([finding({ id: 'a', severity: 'info' }), finding({ id: 'b', severity: 'critical' }), finding({ id: 'c', severity: 'repaired' }), finding({ id: 'd', severity: 'fault' })]))
      expect(check?.result?.findings.map((f) => [f.id, f.severity])).toEqual([['d', 'fault'], ['c', 'repaired'], ['a', 'info'], ['b', 'info']])
    })

    it('ignores unknown keys and fields of the wrong type', () => {
      const check = parseFaultCheck({ ...done([finding({ extra: 1, count: 'two', details: ['kept', 3, null], evidence: 'one line', docs: 'javascript:alert(1)' })]), extra: true })
      expect(check?.result?.findings[0]).toMatchObject({ count: 0, details: ['kept'], evidence: [], docs: null })
      expect(check?.result?.findings[0]).not.toHaveProperty('extra')
    })

    it('reads a check that did not say it read the whole period as partial', () => {
      expect(parseFaultCheck(done([], { complete: false, stopped: 'time' }))?.result?.checked).toMatchObject({ complete: false, stopped: 'time' })
      expect(parseFaultCheck(done([], { complete: 'yes' }))?.result?.checked.complete).toBe(false)
    })
  })

  describe('faultReportName', () => {
    it('names the report for when the check finished, in UTC', () => {
      expect(faultReportName('2026-11-22T12:00:09Z')).toBe('valve-oil-diagnostics-20261122T120009Z.txt')
      expect(faultReportName(null, new Date('2026-11-22T13:04:05.678Z'))).toBe('valve-oil-diagnostics-20261122T130405Z.txt')
    })
  })
})
