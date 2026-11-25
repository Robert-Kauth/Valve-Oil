import { confirm } from '../ui'
import { isActive } from '../../state/job'
import { printer } from '../../state/printer'
import { t } from '../../i18n'
import { send } from './common'

/** Turns heaters off from either temperature surface; print-end macros own active chamber ventilation. */
export function useCoolDownAction() {
  return async () => {
    const active = isActive(printer.get().status.print_stats?.state)
    if (active && !(await confirm({ title: t('control.temps.cool_down_title'), body: t('control.temps.cool_down_body'), confirm: t('control.temps.cool_down'), danger: true }))) return
    send('TURN_OFF_HEATERS')
  }
}
