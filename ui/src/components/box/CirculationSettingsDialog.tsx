import { useEffect, useState } from 'react'
import { CancelButton, Dialog, Field, Notice } from '../ui'
import { errorText } from '../../lib/browser'
import { CIRCULATION_THRESHOLD, CIRCULATION_HYSTERESIS, boxCommands, circulationSettingValues, circulationSettingsBlocker, circulationSettingsValid, refreshBoxCirculation, type BoxState } from '../../state/box'
import { runGcode, usePrinter } from '../../state/printer'
import { t, type MessageKey } from '../../i18n'

const BLOCKER_KEYS: Record<Exclude<ReturnType<typeof circulationSettingsBlocker>, null>, MessageKey> = {
  unavailable: 'box.circulation.unavailable',
  printing: 'box.circulation.blocked_printing',
  heating: 'box.circulation.blocked_heating',
  sensor_fault: 'box.circulation.blocked_sensor_fault',
  circulating: 'box.circulation.blocked_circulating',
}

export function CirculationSettingsDialog({ box, boxNumber, onClose }: { box: BoxState; boxNumber: number; onClose: () => void }) {
  const circulation = box.circulation.find((c) => c.box === boxNumber)
  const values = circulation ? circulationSettingValues(circulation) : null
  const [threshold, setThreshold] = useState(values?.threshold ?? CIRCULATION_THRESHOLD.default)
  const [hysteresis, setHysteresis] = useState(values?.hysteresis ?? CIRCULATION_HYSTERESIS.default)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const ready = usePrinter((s) => s.klippy === 'ready')

  useEffect(() => {
    if (!values) return
    setThreshold(values.threshold)
    setHysteresis(values.hysteresis)
  }, [values?.threshold, values?.hysteresis])

  if (!circulation || !values) return null
  const blocker = ready ? circulationSettingsBlocker(box, circulation) : 'unavailable'
  const blockerText = blocker ? t(BLOCKER_KEYS[blocker]) : null
  const settingsWarning = circulation.settingsError ? t('box.circulation.settings_error', { error: t('box.circulation.range') }) : null
  const valid = circulationSettingsValid(threshold, hysteresis)
  const save = async () => {
    if (blocker || !valid || pending) return
    setPending(true)
    setError('')
    let commandError: unknown
    let refreshError: unknown
    try {
      await runGcode(boxCommands.circulation(boxNumber, threshold, hysteresis))
    } catch (e) {
      commandError = e
    }
    try {
      if (!(await refreshBoxCirculation(boxNumber, threshold, hysteresis))) throw new Error(t('box.circulation.refresh_failed'))
    } catch (e) {
      refreshError = e
    }
    setPending(false)
    if (commandError) setError(errorText(commandError))
    else if (refreshError) setError(errorText(refreshError))
    else onClose()
  }
  return (
    <Dialog
      open
      onClose={onClose}
      title={t('box.circulation.dialog_title', { n: boxNumber })}
      footer={
        <>
          <CancelButton onClick={onClose} />
          <button className="btn btn-primary" disabled={pending || !!blocker || !valid} onClick={save}>
            {t('common.apply')}
          </button>
        </>
      }
    >
      <div className="grid gap-4">
        <Notice tone="warn">{t('box.circulation.threshold_risk')}</Notice>
        {settingsWarning && <Notice tone="warn">{settingsWarning}</Notice>}
        {blockerText && <Notice tone="info">{blockerText}</Notice>}
        {error && <Notice tone="bad">{error}</Notice>}
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label={t('box.circulation.threshold')}>
            <input className="field num" type="number" inputMode="decimal" min={CIRCULATION_THRESHOLD.min} max={CIRCULATION_THRESHOLD.max} step="0.1" disabled={pending || !!blocker} value={threshold} onChange={(e) => setThreshold(Number(e.target.value))} />
          </Field>
          <Field label={t('box.circulation.hysteresis')}>
            <input className="field num" type="number" inputMode="decimal" min={CIRCULATION_HYSTERESIS.min} max={CIRCULATION_HYSTERESIS.max} step="0.1" disabled={pending || !!blocker} value={hysteresis} onChange={(e) => setHysteresis(Number(e.target.value))} />
          </Field>
        </div>
        {!valid && <div className="text-[12.5px] text-bad">{t('box.circulation.range')}</div>}
      </div>
    </Dialog>
  )
}
