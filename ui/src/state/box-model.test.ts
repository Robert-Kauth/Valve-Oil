import { describe, expect, it } from 'vitest'
import type { PrinterState } from './printer'
import { QIDI_BOX_EXTERNAL_SLOT } from '../qidi/qidi-box'
import { boxCommands, boxHeaterMax, deriveBox, dryBlocker, slotActions, slotByIndex, slotOverline, type BoxState, type Lists, type SlotActions } from './box-model'

type Status = PrinterState['status']

const NO_LISTS: Lists = { legacy: null, database: null, databaseError: false }
// Firmware 01.01.07.01's filament database, read from the printer.
const DATABASE: Lists = { legacy: null, database: [{ id: 'QF001', name: 'PLA Rapido', type: 'PLA', vendor: 'QIDI' }], databaseError: false }

// Valve Oil's own Box commands, as Klipper lists them when Valve Oil is installed.
const VALVE_OIL_COMMANDS = ['VO_CUT_FILAMENT', 'VO_BOX_SLOT_JOG', 'VO_BOX_RFID_WRITE']

// Slots are numbered from 0 across Boxes: 1A is 0, 2A is 4, 2B is 5.
const SLOT_2A = 4
const SLOT_2B = 5

/**
 * The Box as the UI derives it from what Klipper reports: two Boxes, switched on, Klipper ready and idle, unless a
 * test says otherwise. `controller` is firmware 01.01.07.01's multi_color_controller, which earlier firmware lacks.
 */
function box({
  controller = true,
  variables = {},
  status = {},
  commands = [],
  objects = [],
  klippy = 'ready',
  lists = NO_LISTS,
}: { controller?: boolean; variables?: Record<string, unknown>; status?: Status; commands?: string[]; objects?: string[]; klippy?: PrinterState['klippy']; lists?: Lists } = {}): BoxState {
  const printer: PrinterState = {
    conn: 'open',
    connLost: false,
    klippy,
    klippyMessage: '',
    objects,
    status: {
      ...(controller ? { multi_color_controller: {} } : {}),
      save_variables: { variables: { box_count: 2, enable_box: 1, slot_sync: 'slot-1', ...variables } },
      print_stats: { state: 'standby' },
      ...status,
    },
    temps: {},
    console: [],
    commands,
    heaterSettings: {},
    settings: {},
    server: null,
    host: null,
    filesRev: 0,
    historyRev: 0,
  }
  return deriveBox(printer, lists)
}

/** Filament at the inlet of each given slot: its box_stepper's runout_button reads 0. */
const inlets = (...slots: number[]): Status => Object.fromEntries(slots.map((s) => [`box_stepper slot${s}`, { runout_button: 0 }]))

/** The actions a slot's buttons offer, by name. */
const offered = (b: BoxState, slot: number) => {
  const actions = slotActions(b, slotByIndex(b, slot)!)
  return (Object.keys(actions) as (keyof SlotActions)[]).filter((k) => actions[k] === true).sort()
}

describe('Box commands on firmware 01.01.07.01', () => {
  it('loads a slot and reads its tag by slot number', () => {
    const b = box()
    expect(boxCommands.load(b, 0)).toBe('LOAD_FILAMENT_CONTROL SLOT=0')
    expect(boxCommands.load(b, SLOT_2B)).toBe('LOAD_FILAMENT_CONTROL SLOT=5')
    expect(boxCommands.rfid(b, SLOT_2B)).toBe('READ_RFID SLOT=5')
  })

  it('unloads and ejects with QIDI’s heated flow when Valve Oil’s cold cut is not installed', () => {
    const b = box()
    expect(boxCommands.unload(b, SLOT_2B)).toBe('UNLOAD_FILAMENT_CONTROL SLOT=5')
    expect(boxCommands.eject(b, SLOT_2B)).toBe('EJECT_FILAMENT_CONTROL SLOT=5')
  })

  it('unloads and ejects cold when Valve Oil’s cold cut is installed, and never loads cold', () => {
    const b = box({ commands: VALVE_OIL_COMMANDS })
    expect(boxCommands.unload(b, SLOT_2B)).toBe('UNLOAD_FILAMENT_CONTROL SLOT=5 COLD=1')
    expect(boxCommands.eject(b, SLOT_2B)).toBe('EJECT_FILAMENT_CONTROL SLOT=5 COLD=1')
    expect(boxCommands.load(b, SLOT_2B)).toBe('LOAD_FILAMENT_CONTROL SLOT=5')
  })

  it('leaves COLD off while a print is paused, when the printer refuses it', () => {
    const b = box({ commands: VALVE_OIL_COMMANDS, status: { print_stats: { state: 'paused' } } })
    expect(boxCommands.unload(b, SLOT_2B)).toBe('UNLOAD_FILAMENT_CONTROL SLOT=5')
    expect(boxCommands.eject(b, SLOT_2B)).toBe('EJECT_FILAMENT_CONTROL SLOT=5')
  })
})

describe('Box commands on firmware before 01.01.07.01', () => {
  it('sends QIDI’s earlier commands in their own parameter forms', () => {
    const b = box({ controller: false })
    expect(boxCommands.load(b, SLOT_2B)).toBe('E_LOAD slot=5')
    expect(boxCommands.unload(b, SLOT_2B)).toBe('E_UNLOAD slot=5')
    expect(boxCommands.eject(b, SLOT_2B)).toBe('E_BOX slot=5')
    expect(boxCommands.rfid(b, SLOT_2B)).toBe('RFID_READ SLOT=slot5')
  })

  it('never adds COLD, which those commands don’t take', () => {
    const b = box({ controller: false, commands: VALVE_OIL_COMMANDS })
    expect(boxCommands.unload(b, 0)).toBe('E_UNLOAD slot=0')
    expect(boxCommands.eject(b, 0)).toBe('E_BOX slot=0')
  })
})

describe('Box tag, jog, tool, setting, and dryer commands', () => {
  it('writes a tag with the slot number, the filament ID, and each color as bare upper-case hex', () => {
    expect(boxCommands.writeTag(3, 'QF001', ['#ff8800'])).toBe('VO_BOX_RFID_WRITE SLOT=3 FILAMENT=QF001 COLORS=FF8800')
    expect(boxCommands.writeTag(SLOT_2A, 'QF012', ['#FFFFFF', '#00aa00', '#0000FF', '#101010'])).toBe('VO_BOX_RFID_WRITE SLOT=4 FILAMENT=QF012 COLORS=FFFFFF,00AA00,0000FF,101010')
  })

  it('jogs a slot toward the toolhead with a positive distance and back with a negative one', () => {
    expect(boxCommands.jog(SLOT_2A, 10)).toBe('VO_BOX_SLOT_JOG SLOT=4 DISTANCE=10')
    expect(boxCommands.jog(SLOT_2A, -10)).toBe('VO_BOX_SLOT_JOG SLOT=4 DISTANCE=-10')
  })

  it('maps a tool to a slot as the quoted slot name QIDI saves', () => {
    expect(boxCommands.mapTool(0, SLOT_2A)).toBe(`SAVE_VARIABLE VARIABLE=value_t0 VALUE="'slot4'"`)
    expect(boxCommands.mapTool(15, QIDI_BOX_EXTERNAL_SLOT)).toBe(`SAVE_VARIABLE VARIABLE=value_t15 VALUE="'slot16'"`)
  })

  it('saves a Box setting as 1 or 0', () => {
    expect(boxCommands.setting('enable_box', true)).toBe('SAVE_VARIABLE VARIABLE=enable_box VALUE=1')
    expect(boxCommands.setting('vo_box_spool_roll', false)).toBe('SAVE_VARIABLE VARIABLE=vo_box_spool_roll VALUE=0')
  })

  it("sends detection on insertion through QIDI's command, which also reaches RS-485 Boxes, only where box485 is configured", () => {
    const rs485 = box({ commands: ['SET_ENABLE_RFID_READ'], objects: ['multi_color_controller', 'box485'] })
    expect(boxCommands.setting('auto_read_rfid', true, rs485)).toBe('SET_ENABLE_RFID_READ ENABLE=1')
    expect(boxCommands.setting('auto_read_rfid', false, rs485)).toBe('SET_ENABLE_RFID_READ ENABLE=0')
    // QIDI's commands for the other toggles only save the variable.
    expect(boxCommands.setting('auto_init_detect', true, rs485)).toBe('SAVE_VARIABLE VARIABLE=auto_init_detect VALUE=1')
    expect(boxCommands.setting('auto_reload_detect', false, rs485)).toBe('SAVE_VARIABLE VARIABLE=auto_reload_detect VALUE=0')
    // Without box485, QIDI's command would stop Klipper looking it up; before 01.01.07.01 there is no such command.
    for (const b of [
      box({ commands: ['SET_ENABLE_RFID_READ'], objects: ['multi_color_controller'] }),
      box({ controller: false, objects: ['box485'] }),
    ]) {
      expect(boxCommands.setting('auto_read_rfid', true, b)).toBe('SAVE_VARIABLE VARIABLE=auto_read_rfid VALUE=1')
    }
  })

  it('selects a tool by its number', () => {
    expect(boxCommands.tool(0)).toBe('T0')
    expect(boxCommands.tool(15)).toBe('T15')
  })

  it('sets and turns off a Box dryer by the heater’s short name', () => {
    const b = box({ status: { 'heater_generic heater_box2': { temperature: 31, target: 0 } } })
    const heater = b.climates.find((c) => c.box === 2)!.heater!
    expect(boxCommands.heater(heater, 45)).toBe('SET_HEATER_TEMPERATURE HEATER=heater_box2 TARGET=45')
    expect(boxCommands.heater(heater, 0)).toBe('SET_HEATER_TEMPERATURE HEATER=heater_box2 TARGET=0')
  })

  it('starts and stops QIDI’s timed drying by Box number, in whole hours', () => {
    expect(boxCommands.dry(2, 45, 12)).toBe('BOX_START_DRYING BOX=2 TEMP=45 TIME=12')
    expect(boxCommands.stopDry(2)).toBe('BOX_STOP_DRYING BOX=2')
  })

  it('retries or stops a paused flow with QIDI’s commands, and cuts with Valve Oil’s', () => {
    expect(boxCommands.retry()).toBe('FLOW_RETRY')
    expect(boxCommands.stop()).toBe('FLOW_STOP')
    expect(boxCommands.cut()).toBe('VO_CUT_FILAMENT')
  })
})

describe('deriveBox', () => {
  const commandName = (gcode: string) => gcode.split(' ')[0]

  it('finds Valve Oil’s cut, jog, and tag writing by the commands those actions send', () => {
    const without = box({ lists: DATABASE })
    expect([without.canCut, without.canCold, without.canJog, without.canWriteTag]).toEqual([false, false, false, false])
    const sent = [boxCommands.cut(), commandName(boxCommands.jog(0, 10)), commandName(boxCommands.writeTag(0, 'QF001', ['#FFFFFF']))]
    const installed = box({ lists: DATABASE, commands: sent })
    expect([installed.canCut, installed.canCold, installed.canJog, installed.canWriteTag]).toEqual([true, true, true, true])
  })

  it('writes tags only with the firmware filament database, whose IDs the tags carry', () => {
    expect(box({ commands: VALVE_OIL_COMMANDS, lists: NO_LISTS }).canWriteTag).toBe(false)
    expect(box({ controller: false, commands: VALVE_OIL_COMMANDS, lists: DATABASE }).canWriteTag).toBe(false)
  })

  it('reads the tool map in the form the mapping command saves it, skipping tools with no slot', () => {
    const b = box({ variables: { value_t0: 'slot4', value_t2: 'slot5', value_t3: 'slot5', value_t4: 'slot-1', value_t5: 16 } })
    expect(b.toolMap).toEqual({ 0: 4, 2: 5, 3: 5 })
    expect(b.toolsBySlot).toEqual({ 4: ['T0'], 5: ['T2', 'T3'] })
  })

  it('offers tool buttons for mapped Box slots only while the Box is switched on', () => {
    const variables = { value_t0: 'slot0', value_t1: 'slot5', value_t2: 'slot16' }
    const on = box({ variables })
    expect(on.toolButtons.map((t) => boxCommands.tool(t.tool))).toEqual(['T0', 'T1'])
    expect(box({ variables: { ...variables, enable_box: 0 } }).toolButtons).toEqual([])
  })
})

describe('slotActions', () => {
  it('offers Load, Eject, and Read RFID for filament waiting at a slot’s inlet', () => {
    expect(offered(box({ status: inlets(SLOT_2B) }), SLOT_2B)).toEqual(['eject', 'load', 'rfid'])
  })

  it('offers Unload instead of Load for the last-loaded slot while its filament is at the hub', () => {
    const b = box({ variables: { last_load_slot: 'slot5' }, status: { ...inlets(SLOT_2A, SLOT_2B), box_extras: { b_endstop_state: 0 } } })
    expect(offered(b, SLOT_2B)).toEqual(['eject', 'rfid', 'unload'])
    expect(offered(b, SLOT_2A)).toEqual(['eject', 'load', 'rfid'])
  })

  it('offers an empty slot only what works without filament: jog, cut, and writing the spool’s tag', () => {
    expect(offered(box(), SLOT_2B)).toEqual([])
    expect(offered(box({ commands: VALVE_OIL_COMMANDS, lists: DATABASE }), SLOT_2B)).toEqual(['cut', 'jog', 'writeTag'])
  })

  it('offers the external spool no Box motion or tag actions', () => {
    const b = box({ commands: VALVE_OIL_COMMANDS, lists: DATABASE, status: inlets(SLOT_2B) })
    expect(offered(b, QIDI_BOX_EXTERNAL_SLOT)).toEqual(['cut', 'edit'])
  })

  it.each<[string, Parameters<typeof box>[0]]>([
    ['a print is running', { status: { ...inlets(SLOT_2B), print_stats: { state: 'printing' } } }],
    ['Klipper is not ready', { status: inlets(SLOT_2B), klippy: 'shutdown' }],
    ['a Box flow is running', { status: { ...inlets(SLOT_2B), multi_color_controller: { task: { current_queue: ['BOX_LOAD'], current_step: 'BOX_LOAD', is_paused: false } } } }],
    ['the Box’s button is busy', { status: { ...inlets(SLOT_2B), box_extras: { box_button_state: 1 } } }],
  ])('offers nothing while %s', (_, options) => {
    expect(offered(box({ commands: VALVE_OIL_COMMANDS, lists: DATABASE, ...options }), SLOT_2B)).toEqual([])
  })

  it('still loads, ejects, and jogs while a print is paused, but doesn’t cut', () => {
    const b = box({ commands: VALVE_OIL_COMMANDS, lists: DATABASE, status: { ...inlets(SLOT_2B), print_stats: { state: 'paused' } } })
    expect(offered(b, SLOT_2B)).toEqual(['edit', 'eject', 'jog', 'load', 'rfid', 'writeTag'])
  })

  it('jogs, and offers nothing else, while a failed Box step waits for Retry or Stop', () => {
    const failed = { task: { current_queue: ['BOX_UNLOAD'], current_step: 'BOX_UNLOAD', is_paused: true } }
    const b = box({ commands: VALVE_OIL_COMMANDS, lists: DATABASE, status: { ...inlets(SLOT_2B), multi_color_controller: failed } })
    expect(offered(b, SLOT_2B)).toEqual(['jog'])
  })

  describe('with filament stopped between the hub and the toolhead', () => {
    // The hub sensor sees filament, the toolhead sensor doesn't, and the failed flow is paused.
    const stranded: Status = {
      ...inlets(SLOT_2A, SLOT_2B),
      box_extras: { b_endstop_state: 0 },
      'filament_switch_sensor filament_switch_sensor': { filament_detected: false },
      multi_color_controller: { task: { current_queue: ['BOX_LOAD'], current_step: 'BOX_LOAD', is_paused: true } },
    }

    it('offers Unload and Eject for the slot at the hub and nothing for the others', () => {
      const b = box({ variables: { vo_box_hub_slot: 'slot5' }, status: stranded })
      expect(offered(b, SLOT_2B)).toEqual(['eject', 'unload'])
      expect(offered(b, SLOT_2A)).toEqual([])
    })

    it('offers Unload and Eject for every loaded slot when the slot at the hub is unknown', () => {
      const b = box({ status: stranded })
      expect(offered(b, SLOT_2A)).toEqual(['eject', 'unload'])
      expect(offered(b, SLOT_2B)).toEqual(['eject', 'unload'])
    })
  })
})

describe('the external spool with no Box', () => {
  // Every supported firmware has QIDI's controller, which runs the external spool's flows without a Box.
  const toolhead = (detected: boolean | undefined): Status =>
    detected === undefined ? {} : { 'filament_switch_sensor filament_switch_sensor': { filament_detected: detected } }
  const noBox = (detected: boolean | undefined, options: Parameters<typeof box>[0] = {}) =>
    box({ commands: VALVE_OIL_COMMANDS, lists: DATABASE, ...options, variables: { box_count: 0, slot_sync: 'slot16', ...options.variables }, status: { ...toolhead(detected), ...options.status } })

  it('loads into an empty toolhead and unloads what the toolhead sensor detects', () => {
    expect(offered(noBox(false), QIDI_BOX_EXTERNAL_SLOT)).toEqual(['cut', 'edit', 'load'])
    expect(offered(noBox(true), QIDI_BOX_EXTERNAL_SLOT)).toEqual(['cut', 'edit', 'unload'])
  })

  it('offers neither without a toolhead sensor reading, or without QIDI’s controller', () => {
    expect(offered(noBox(undefined), QIDI_BOX_EXTERNAL_SLOT)).toEqual(['cut', 'edit'])
    expect(offered(noBox(true, { controller: false }), QIDI_BOX_EXTERNAL_SLOT)).not.toContain('unload')
  })

  it.each<[string, Parameters<typeof box>[0]]>([
    ['a print is running', { status: { print_stats: { state: 'printing' } } }],
    ['Klipper is not ready', { klippy: 'shutdown' }],
    ['a flow is running', { status: { multi_color_controller: { task: { current_queue: ['EXT_UNLOAD'], current_step: 'EXT_UNLOAD', is_paused: false } } } }],
  ])('offers neither while %s', (_, options) => {
    expect(offered(noBox(true, options), QIDI_BOX_EXTERNAL_SLOT)).not.toContain('unload')
    expect(offered(noBox(false, options), QIDI_BOX_EXTERNAL_SLOT)).not.toContain('load')
  })

  it('still unloads and loads while a print is paused, for a spool that ran out', () => {
    expect(offered(noBox(true, { status: { print_stats: { state: 'paused' } } }), QIDI_BOX_EXTERNAL_SLOT)).toContain('unload')
    expect(offered(noBox(false, { status: { print_stats: { state: 'paused' } } }), QIDI_BOX_EXTERNAL_SLOT)).toContain('load')
  })

  it('reads nothing into a hub sensor reading, which needs a Box', () => {
    const b = noBox(false, { status: { box_extras: { b_endstop_state: 0 }, multi_color_controller: { task: { current_queue: ['EXT_UNLOAD'], current_step: 'EXT_UNLOAD', is_paused: true } } } })
    expect(b.strandedRecovery).toBe(false)
    expect(slotActions(b, slotByIndex(b, QIDI_BOX_EXTERNAL_SLOT)!).hint).toBeNull()
  })

  it('leaves the external spool to the Box’s flows when a Box is connected', () => {
    const b = box({ commands: VALVE_OIL_COMMANDS, lists: DATABASE, status: toolhead(false) })
    expect(offered(b, QIDI_BOX_EXTERNAL_SLOT)).toEqual(['cut', 'edit'])
  })

  it('sends QIDI’s controller commands for slot 16, and never unloads it cold', () => {
    const b = noBox(true)
    expect(boxCommands.load(b, QIDI_BOX_EXTERNAL_SLOT)).toBe('LOAD_FILAMENT_CONTROL SLOT=16')
    expect(boxCommands.unload(b, QIDI_BOX_EXTERNAL_SLOT)).toBe('UNLOAD_FILAMENT_CONTROL SLOT=16')
    expect(boxCommands.unload(box({ commands: VALVE_OIL_COMMANDS }), SLOT_2B)).toBe('UNLOAD_FILAMENT_CONTROL SLOT=5 COLD=1')
  })

  it('shows the external spool loaded exactly while the toolhead sensor detects filament', () => {
    expect(noBox(true).loadedSlot).toBe(QIDI_BOX_EXTERNAL_SLOT)
    expect(noBox(false).loadedSlot).toBeNull()
    expect(slotOverline(noBox(true), slotByIndex(noBox(true), QIDI_BOX_EXTERNAL_SLOT)!)).toBe('Loaded to the toolhead')
    expect(slotOverline(noBox(false), slotByIndex(noBox(false), QIDI_BOX_EXTERNAL_SLOT)!)).toBe('Not loaded')
  })

  it('waits, rather than failing, while the load’s grip or load step has no filament at the toolhead', () => {
    const at = (step: string, detected: boolean) =>
      noBox(detected, { status: { multi_color_controller: { task: { current_queue: [step, 'EXT_WIPE'], current_step: step, is_paused: true } } } })
    expect(at('EXT_BITE', false).waitingForFilament).toBe(true)
    expect(at('EXT_LOAD', false).waitingForFilament).toBe(true)
    expect(at('EXT_BITE', true).waitingForFilament).toBe(false)
    expect(at('EXT_WIPE', false).waitingForFilament).toBe(false)
  })
})

describe('boxHeaterMax', () => {
  // Each slot's Box limit as firmware 01.01.07.01 reports it, by slot number; null for a filament with none.
  const limits = (bySlot: Record<number, number | null>, external?: number): Status => ({
    box_extras: {
      boxes: [0, 1].map((b) => ({ filament_info: [0, 1, 2, 3].filter((i) => b * 4 + i in bySlot).map((i) => ({ index: i, box_max_temp: bySlot[b * 4 + i] })) })),
    },
    ...(external != null ? { multi_color_controller: { extra: { filament_info: { box_max_temp: external } } } } : {}),
  })

  it('is the lowest Box limit among the filaments loaded in that Box', () => {
    const b = box({ status: { ...limits({ 0: 55, 1: 45, 4: 60 }), ...inlets(0, 1, 4) } })
    expect(boxHeaterMax(b, 1, 65)).toBe(45)
    expect(boxHeaterMax(b, 2, 65)).toBe(60)
  })

  it('ignores an empty slot’s remembered filament, the other Box, and the external spool', () => {
    const b = box({ status: { ...limits({ 0: 55, 1: 40, 4: 35 }, 30), ...inlets(0, 4) } })
    expect(boxHeaterMax(b, 1, 65)).toBe(55)
  })

  it('forbids heating a Box that holds a filament with a limit of 0', () => {
    const b = box({ status: { ...limits({ 0: 55, 1: 0 }), ...inlets(0, 1) } })
    expect(boxHeaterMax(b, 1, 65)).toBe(0)
  })

  it('never goes above the heater’s configured maximum', () => {
    const b = box({ status: { ...limits({ 0: 60 }), ...inlets(0) } })
    expect(boxHeaterMax(b, 1, 50)).toBe(50)
  })

  it('falls back to the heater’s configured maximum, or 65 °C without one, when no loaded filament has a limit', () => {
    const b = box({ status: { ...limits({ 0: null }), ...inlets(0) } })
    expect(boxHeaterMax(b, 1, 70)).toBe(70)
    expect(boxHeaterMax(b, 1, undefined)).toBe(65)
    expect(boxHeaterMax(box(), 2, undefined)).toBe(65)
  })
})

describe('dryBlocker', () => {
  // Drying turns every slot's motor, so it must never run with filament fed into the Box, or during a print.
  it('allows drying a Box with no filament in any slot, whatever the other Box holds', () => {
    const b = box({ status: inlets(SLOT_2A) })
    expect(dryBlocker(b, 1)).toBeNull()
  })

  it('refuses a Box with filament at a slot’s inlet sensor, naming every such slot', () => {
    const b = box({ status: inlets(SLOT_2A, SLOT_2B) })
    const why = dryBlocker(b, 2)
    expect(why).toMatch(/2A/)
    expect(why).toMatch(/2B/)
  })

  it('refuses a Box with a slot loaded to the toolhead, but not the other Box', () => {
    const b = box({ variables: { slot_sync: 'slot5' }, status: inlets(SLOT_2B) })
    expect(dryBlocker(b, 2)).toMatch(/2B/)
    expect(dryBlocker(b, 1)).toBeNull()
  })

  it.each(['printing', 'paused'])('refuses while a print is %s', (state) => {
    const b = box({ status: { print_stats: { state } } })
    expect(dryBlocker(b, 1)).not.toBeNull()
  })
})
