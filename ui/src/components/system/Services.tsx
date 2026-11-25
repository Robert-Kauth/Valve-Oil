import { useState, type ReactNode } from 'react'
import { Cog, Play, Power, PowerOff, RotateCw, Square, Zap } from 'lucide-react'
import { Card, Empty, Spinner, confirm } from '../ui'
import { attempt, toast } from '../toast'
import { client, printer, usePrinter } from '../../state/printer'
import { isActive, useJobActive } from '../../state/job'
import { readLiveUpdateState, updateRunningNow, useUpdateRunning } from '../../state/valve-oil'
import { serviceTone } from './bits'
import { refreshSystemInfo, useSystemInfo } from './system-info'
import { t } from '../../i18n'

// What each service on a QIDI printer is, for people who don't know the unit names.
const about = (): Record<string, string> => ({
  klipper: t('system.services.about.klipper'),
  moonraker: t('system.services.about.moonraker'),
  'klipper-mcu': t('system.services.about.klipper_mcu'),
  'qidi-client': t('system.services.about.qidi_client'),
  crowsnest: t('system.services.about.crowsnest'),
  'vo-remote-screen': t('system.services.about.remote_screen'),
  'vo-status': t('system.services.about.status'),
  'moonraker-obico': 'Obico',
  octoeverywhere: 'OctoEverywhere',
  tailscaled: 'Tailscale',
  'zerotier-one': 'ZeroTier',
})

const printStops = () => t('system.services.print_stops')
const jobActiveNow = () => isActive(printer.get().status.print_stats?.state)

type ConfirmedPrintState = 'active' | 'idle'
type ServiceAction = 'start' | 'stop' | 'restart'
type KlipperAction = 'restart' | 'firmware-restart' | 'service-restart'
type HostPowerAction = 'reboot' | 'shutdown'
type ActionDispatch = 'dispatched' | 'blocked' | 'failed'

const updateSensitiveService = (name: string) => name === 'klipper' || name === 'moonraker'
const printSensitiveService = (name: string, action: ServiceAction) => name === 'klipper' || (name === 'klipper-mcu' && action !== 'start')

/**
 * Asks whether to go ahead when Valve Oil's status service can't say whether an update runs. Restarting Klipper or
 * rebooting is how an operator recovers a stuck status service, so an unanswered check asks instead of refusing.
 */
export type UncheckedUpdate = () => Promise<boolean>
const askUnchecked: UncheckedUpdate = () =>
  confirm({ title: t('system.services.unchecked_title'), body: t('system.services.unchecked_body'), confirm: t('system.services.unchecked_confirm'), danger: true })

/** Whether an update allows the action now: refused while one runs, asked about when the status service can't say. */
async function updateAllows(lockedMessage: string, unchecked: UncheckedUpdate): Promise<boolean> {
  const state = await readLiveUpdateState()
  if (state === 'idle') return true
  if (state === 'unknown' && (await unchecked()) && !updateRunningNow()) return true
  if (state === 'running' || updateRunningNow()) toast(lockedMessage, 'error')
  return false
}

// The same checks run when the operator confirms and again just before the action is sent.
const serviceBlocked = (name: string, action: ServiceAction, printing: boolean) =>
  updateSensitiveService(name) && updateRunningNow() ? t('system.services.locked') : !printing && jobActiveNow() && printSensitiveService(name, action) ? printStops() : null
const printerBlocked = (lockedMessage: string, printing: boolean) => (updateRunningNow() ? lockedMessage : !printing && jobActiveNow() ? printStops() : null)

export async function dispatchServiceAction(name: string, action: ServiceAction, confirmedPrintState: ConfirmedPrintState, unchecked = askUnchecked): Promise<ActionDispatch> {
  if (updateSensitiveService(name) && !(await updateAllows(t('system.services.locked'), unchecked))) return 'blocked'
  if (confirmedPrintState === 'idle' && jobActiveNow() && printSensitiveService(name, action)) {
    toast(printStops(), 'error')
    return 'blocked'
  }
  return (await attempt(async () => (await client.call(`machine.services.${action}`, { service: name }, 90000), true))) ? 'dispatched' : 'failed'
}

export async function dispatchKlipperAction(action: KlipperAction, confirmedPrintState: ConfirmedPrintState, unchecked = askUnchecked): Promise<ActionDispatch> {
  if (!(await updateAllows(t('system.services.locked'), unchecked))) return 'blocked'
  if (confirmedPrintState === 'idle' && jobActiveNow()) {
    toast(printStops(), 'error')
    return 'blocked'
  }
  const sent = await attempt(async () => {
    if (action === 'restart') await client.call('printer.restart')
    else if (action === 'firmware-restart') await client.call('printer.firmware_restart')
    else await client.call('machine.services.restart', { service: 'klipper' }, 90000)
    return true
  })
  return sent ? 'dispatched' : 'failed'
}

export async function dispatchHostPowerAction(what: HostPowerAction, confirmedPrintState: ConfirmedPrintState, unchecked = askUnchecked): Promise<ActionDispatch> {
  if (!(await updateAllows(t('system.services.host.update_locked'), unchecked))) return 'blocked'
  if (confirmedPrintState === 'idle' && jobActiveNow()) {
    toast(printStops(), 'error')
    return 'blocked'
  }
  // Moonraker goes away before it answers, so a lost connection here is expected.
  client.call(`machine.${what}`).catch(() => {})
  toast(what === 'reboot' ? t('system.services.host.rebooting') : t('system.services.host.shutting_down'))
  return 'dispatched'
}

export function Services() {
  const { system, loading } = useSystemInfo()
  const printing = useJobActive()
  const updating = useUpdateRunning()
  const services = Object.entries(system?.service_state ?? {}).sort(([a], [b]) => order(a) - order(b) || a.localeCompare(b))
  const canHost = system?.virtualization?.virt_type !== 'container'
  return (
    <div className="grid grid-cols-1 items-start gap-3.5 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
      <Card title={t('system.services.title')} icon={<Cog size={15} />} aside={loading ? <Spinner size={14} /> : undefined}>
        {services.length === 0 ? (
          <Empty>{loading ? t('common.loading') : t('system.services.none')}</Empty>
        ) : (
          <div className="grid grid-cols-1 gap-2">
            {services.map(([name, st]) => (
              <ServiceRow key={name} name={name} active={st.active_state} sub={st.sub_state} printing={printing} locked={updating && updateSensitiveService(name)} />
            ))}
          </div>
        )}
        {updating && <p className="mb-0 text-[12.5px] text-ink-3">{t('system.services.locked')}</p>}
      </Card>
      <div className="grid min-w-0 grid-cols-1 gap-3.5">
        {!updating && <KlipperCard printing={printing} />}
        {canHost && <HostPowerCard printing={printing} updating={updating} />}
      </div>
    </div>
  )
}

const FIRST = ['klipper', 'moonraker', 'qidi-client', 'klipper-mcu', 'crowsnest']
const order = (n: string) => (FIRST.includes(n) ? FIRST.indexOf(n) : FIRST.length)

function ServiceRow({ name, active, sub, printing, locked }: { name: string; active: string; sub: string; printing: boolean; locked: boolean }) {
  const [busy, setBusy] = useState<string | null>(null)
  const stopped = active === 'inactive' || active === 'failed'
  const act = async (action: ServiceAction) => {
    if (!(await ask(name, action, printing))) return
    setBusy(action)
    const result = await dispatchServiceAction(name, action, printing ? 'active' : 'idle')
    setBusy(null)
    if (result === 'dispatched' && name !== 'moonraker') toast(action === 'stop' ? t('system.services.stopped', { name }) : action === 'start' ? t('system.services.started', { name }) : t('system.services.restarted', { name }))
    // Moonraker announces most changes; refresh in case this service isn't one it watches.
    if (result !== 'blocked') refreshSystemInfo()
  }
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-xl bg-inset px-3 py-2.5" style={{ boxShadow: 'inset 0 0 0 1px var(--line)' }}>
      <div className="min-w-0 flex-1 basis-48">
        <div className="flex flex-wrap items-center gap-2">
          <span className="code font-semibold text-ink">{name}</span>
          <span className={`pill ${serviceTone(active)} py-0.5 text-[11.5px]`}>
            {active}
            {sub && sub !== active ? ` (${sub})` : ''}
          </span>
        </div>
        {about()[name] && <div className="mt-0.5 text-[12.5px] text-ink-3">{about()[name]}</div>}
      </div>
      <div className="flex min-w-0 max-w-full flex-wrap gap-1.5">
        <button className="btn btn-sm" disabled={locked || busy != null} onClick={() => act(stopped ? 'start' : 'restart')}>
          {busy === (stopped ? 'start' : 'restart') ? <Spinner size={13} /> : stopped ? <Play size={14} /> : <RotateCw size={14} />} {stopped ? t('system.services.start') : t('system.services.restart')}
        </button>
        {/* Stopping Moonraker would take this page with it, with no way back but SSH, so Fluidd hides it too. */}
        {name !== 'moonraker' && (
          <button className="btn btn-sm btn-danger" disabled={locked || busy != null || stopped} onClick={() => act('stop')}>
            {busy === 'stop' ? <Spinner size={13} /> : <Square size={13} />} {t('system.services.stop')}
          </button>
        )}
      </div>
    </div>
  )
}

function ask(name: string, action: ServiceAction, printing: boolean): Promise<boolean> {
  const bodies: ReactNode[] = []
  if (name === 'klipper' && action !== 'start') bodies.push(action === 'stop' ? t('system.services.ask.klipper_stop') : t('system.services.ask.klipper_restart'))
  if (name === 'moonraker') bodies.push(t('system.services.ask.moonraker'))
  if (name === 'qidi-client') bodies.push(action === 'stop' ? t('system.services.ask.qidi_stop') : t('system.services.ask.qidi_restart'))
  if (printing && printSensitiveService(name, action)) bodies.push(printStops())
  if (action === 'start' && !printing) return Promise.resolve(true)
  return confirm({
    title: t(`system.services.ask.${action}_title`, { name }),
    body: bodies.length ? bodies.join(' ') : t(`system.services.ask.${action}_body`, { name }),
    confirm: t(`system.services.${action}`),
    danger: action !== 'start',
    validate: () => serviceBlocked(name, action, printing),
  })
}

function KlipperCard({ printing }: { printing: boolean }) {
  const klippy = usePrinter((s) => s.klippy)
  const message = usePrinter((s) => s.klippyMessage)
  const connected = klippy !== 'disconnected' && klippy !== 'unknown'
  const [busy, setBusy] = useState<string | null>(null)
  const run = async (key: string, title: string, body: string, label: string, action: KlipperAction) => {
    if (
      !(await confirm({
        title,
        body: printing ? `${body} ${printStops()}` : body,
        confirm: label,
        danger: true,
        validate: () => printerBlocked(t('system.services.locked'), printing),
      }))
    )
      return
    setBusy(key)
    await dispatchKlipperAction(action, printing ? 'active' : 'idle')
    setBusy(null)
  }
  // One of the card's buttons: what it asks first, then what it runs.
  const button = (key: string, Icon: typeof Zap, hint: string, title: string, body: string, label: string, action: KlipperAction) => (
    <button className="btn" disabled={busy != null} title={hint} onClick={() => run(key, title, body, label, action)}>
      {busy === key ? <Spinner size={14} /> : <Icon size={15} />} {label}
    </button>
  )
  return (
    <Card title="Klipper" icon={<Zap size={15} />} aside={<span className={`pill ${klippy === 'ready' ? 'tone-good' : klippy === 'startup' ? 'tone-warn' : 'tone-bad'} py-0.5 text-[11.5px]`}>{klippy}</span>}>
      {klippy !== 'ready' && message && <pre className="code m-0 mb-3 max-h-48 overflow-auto rounded-lg bg-inset p-2.5 whitespace-pre-wrap">{message}</pre>}
      <div className="grid gap-2 sm:grid-cols-2">
        {connected ? (
          <>
            {button('restart', RotateCw, t('system.services.klipper.restart_hint'), t('system.services.klipper.restart_title'), t('system.services.klipper.restart_body'), t('system.services.klipper.restart'), 'restart')}
            {button('fw', Zap, t('system.services.klipper.fw_hint'), t('system.services.klipper.fw_title'), t('system.services.klipper.fw_body'), t('system.services.klipper.fw'), 'firmware-restart')}
          </>
        ) : (
          button('svc', RotateCw, t('system.services.klipper.svc_hint'), t('system.services.klipper.restart_title'), t('system.services.klipper.svc_body'), t('system.services.klipper.restart'), 'service-restart')
        )}
      </div>
    </Card>
  )
}

function HostPowerCard({ printing, updating }: { printing: boolean; updating: boolean }) {
  const go = async (what: 'reboot' | 'shutdown') => {
    const reboot = what === 'reboot'
    const ok = await confirm({
      title: reboot ? t('system.services.host.reboot_title') : t('system.services.host.shutdown_title'),
      body: `${reboot ? t('system.services.host.reboot_body') : t('system.services.host.shutdown_body')}${printing ? ` ${printStops()}` : ''}`,
      confirm: reboot ? t('system.services.host.reboot') : t('system.services.host.shutdown'),
      danger: true,
      validate: () => printerBlocked(t('system.services.host.update_locked'), printing),
    })
    if (!ok) return
    await dispatchHostPowerAction(what, printing ? 'active' : 'idle')
  }
  return (
    <Card title={t('system.overview.host.title')} icon={<Power size={15} />}>
      <p className="mt-0 mb-3 text-ink-3">{t('system.services.host.about')}</p>
      {updating && <p className="tone-warn mb-3 rounded-lg px-3 py-2 text-[13px]">{t('system.services.host.update_locked')}</p>}
      <div className="grid gap-2 sm:grid-cols-2">
        <button className="btn btn-danger" disabled={updating} onClick={() => go('reboot')}>
          <RotateCw size={15} /> {t('system.services.host.reboot')}
        </button>
        <button className="btn btn-danger" disabled={updating} onClick={() => go('shutdown')}>
          <PowerOff size={15} /> {t('system.services.host.shutdown')}
        </button>
      </div>
    </Card>
  )
}
