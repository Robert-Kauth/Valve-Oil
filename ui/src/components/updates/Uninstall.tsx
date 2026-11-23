import { useEffect, useState } from 'react'
import { CircleCheck, CircleX, ExternalLink, LoaderCircle, Trash2, TriangleAlert } from 'lucide-react'
import { Dialog, Notice } from '../ui'
import { LogoMark } from '../Logo'
import { JobView, RunningBox } from '../settings/Addons'
import { fluiddUrl } from '../../lib/links'
import { voSentence, VO_UPDATE_NOW_BLOCKERS, type VoStatusBlocker } from '../../qidi/vo-status'
import { useValveOil, type ValveOilState } from '../../state/valve-oil'
import {
  checkUninstall,
  clearUninstallError,
  closeUninstall,
  startUninstall,
  uninstallDisabled,
  useUninstall,
  UNINSTALL_POLL_MS,
} from '../../state/uninstall'
import { t, type MessageKey } from '../../i18n'

// Uninstall's own reasons; the rest are Update now's, worded the same.
const OWN_BLOCKERS = ['firmware_update_pending', 'installer_missing']

function blockedText(blockedBy: VoStatusBlocker[]) {
  if (blockedBy.length === 0) return t('updates.blocked.none')
  return blockedBy
    .map((b) =>
      OWN_BLOCKERS.includes(b.code)
        ? t(`updates.uninstall.blocked.${b.code}` as MessageKey)
        : VO_UPDATE_NOW_BLOCKERS.includes(b.code)
          ? t(`updates.blocked.code.${b.code}` as MessageKey)
          : b.code === 'recovery_required'
            ? voSentence(b.message)
            : t('updates.blocked.other', { message: b.message }),
    )
    .join(' ')
}

/** The Valve Oil card's last row: uninstalling, after a question that says what it does and asks about the OS optimizations. */
export function UninstallRow({ s }: { s: ValveOilState }) {
  const u = useUninstall()
  const [confirm, setConfirm] = useState(false)
  const [undoOptimizations, setUndoOptimizations] = useState(true)
  const offer = s.status?.uninstall
  if (!offer) return null
  const disabled = uninstallDisabled(s, u)

  return (
    <>
      <div className="sep my-2" />
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 py-1">
        <div className="min-w-[180px] flex-1">
          <div className="font-medium">{t('updates.uninstall.title')}</div>
          <div className="text-[12.5px] text-ink-3">{t('updates.uninstall.sub')}</div>
        </div>
        <div className="ml-auto grid max-w-[320px] justify-items-end gap-1">
          <button className="btn btn-sm btn-danger" disabled={disabled} onClick={() => setConfirm(true)}>
            {u.pending ? <LoaderCircle size={13} className="spin" /> : <Trash2 size={13} />}
            {t('updates.uninstall.button')}
          </button>
          {!offer.allowed && <div className="text-right text-[12px] text-ink-3">{blockedText(offer.blocked_by)}</div>}
        </div>
      </div>
      {u.error && (
        <Notice tone="bad" onClose={clearUninstallError}>
          {u.blockedBy.length > 0 ? t('updates.uninstall.blocked_now', { reasons: blockedText(u.blockedBy) }) : t('updates.uninstall.error', { error: u.error })}
        </Notice>
      )}

      <Dialog
        open={confirm}
        onClose={() => setConfirm(false)}
        title={t('updates.uninstall.title')}
        footer={
          <>
            <button className="btn" onClick={() => setConfirm(false)}>
              {t('common.cancel')}
            </button>
            <button
              className="btn btn-danger"
              disabled={disabled}
              onClick={() => {
                setConfirm(false)
                startUninstall(!undoOptimizations)
              }}
            >
              {t('updates.uninstall.button')}
            </button>
          </>
        }
      >
        <div className="grid gap-3">
          <div className="tone-warn flex items-start gap-2.5 rounded-lg px-3 py-2.5">
            <TriangleAlert size={16} className="mt-0.5 flex-none" />
            <span className="text-ink">{t('updates.uninstall.confirm')}</span>
          </div>
          <label className="flex items-start gap-2.5">
            <input type="checkbox" className="mt-1 h-4 w-4 flex-none accent-[var(--accent)]" checked={undoOptimizations} onChange={(e) => setUndoOptimizations(e.target.checked)} />
            <span>
              <span className="font-medium">{t('updates.uninstall.undo_optimizations')}</span>
              <span className="block text-[12.5px] text-ink-3">{t('updates.uninstall.undo_optimizations_sub')}</span>
            </span>
          </label>
        </div>
      </Dialog>
    </>
  )
}

/**
 * The whole page while Valve Oil uninstalls: the rest of Valve Oil UI is on its way out. Ends leading to Fluidd, or,
 * when the uninstall stopped, with its output and back to the page.
 */
export function UninstallScreen() {
  const u = useUninstall()
  const s = useValveOil()
  // The uninstall a page didn't start shows from the status until this screen's own answer arrives.
  const job = u.job ?? (s.status?.job?.kind === 'uninstall' ? s.status.job : null)
  const phase = u.phase ?? 'running'

  useEffect(() => {
    if (phase !== 'running') return
    let stopped = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const tick = async () => {
      await checkUninstall()
      if (!stopped) timer = setTimeout(tick, UNINSTALL_POLL_MS)
    }
    tick()
    return () => {
      stopped = true
      clearTimeout(timer)
    }
  }, [phase])

  return (
    <div className="h-full overflow-auto">
      <div className="mx-auto mt-[8vh] grid w-full max-w-[620px] justify-items-center gap-3 p-6 text-center">
        <LogoMark height={84} />
        <div className="mt-2 flex items-center gap-2.5 text-[22px] font-bold">
          {phase === 'running' ? <LoaderCircle size={22} className="spin text-accent-ink" /> : phase === 'done' ? <CircleCheck size={22} className="text-good" /> : <CircleX size={22} className="text-bad" />}
          {phase === 'running' ? t('updates.uninstall.running_title') : phase === 'done' ? t('updates.uninstall.done_title') : t('updates.uninstall.failed_title')}
        </div>
        <div className="w-full text-left">
          {phase === 'running' && (
            <RunningBox title={<span className="text-warn">{t('updates.uninstall.running_body')}</span>}>
              {job?.message && <div className="text-[12.5px] text-ink-3 [overflow-wrap:anywhere]">{job.message}</div>}
            </RunningBox>
          )}
          {phase === 'done' && <div className="text-center text-ink-2">{t('updates.uninstall.done_body')}</div>}
          {phase === 'failed' && (
            <JobView id={job?.id ?? ''} failed log={job?.log ?? []} show={t('updates.progress.show_log')} hide={t('updates.progress.hide_log')}>
              <Notice tone="bad">
                {job?.message ? `${job.message} ` : ''}
                {t('updates.uninstall.failed_body')}
              </Notice>
            </JobView>
          )}
        </div>
        {phase === 'done' && (
          <a className="btn btn-primary btn-lg" href={fluiddUrl()}>
            <ExternalLink size={16} /> {t('updates.uninstall.open_fluidd')}
          </a>
        )}
        {phase === 'failed' && (
          <button className="btn" onClick={closeUninstall}>
            {t('common.close')}
          </button>
        )}
      </div>
    </div>
  )
}
