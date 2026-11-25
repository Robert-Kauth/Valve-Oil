// Home's card for the external spool on a printer with no QIDI Box: what it holds, editing it, and loading and
// unloading it, as the touchscreen does. With a Box, the Box card shows the external spool with the slots.
import { CircleDot, Disc3 } from 'lucide-react'
import { Operation } from '../box/Operation'
import { SlotPanel } from '../box/SlotPanel'
import { slotByIndex, useBox, QIDI_BOX_EXTERNAL_SLOT } from '../../state/box'
import { href } from '../../lib/router'
import { t } from '../../i18n'

export function ExternalSpoolDeck() {
  const box = useBox()
  const slot = slotByIndex(box, QIDI_BOX_EXTERNAL_SLOT)
  if (!box.hasController || !slot) return null
  return (
    <section className="card">
      <h3 className="card-title">
        <CircleDot size={14} />
        {t('box.external_spool')}
        <span className="aside flex items-center gap-0.5">
          <a href={href('filaments')} className="iconbtn h-7 w-7" title={t('home.box.filament_library')} aria-label={t('home.box.filament_library')}>
            <Disc3 size={15} />
          </a>
        </span>
      </h3>
      <div className="grid gap-3">
        {box.task && <Operation box={box} />}
        <SlotPanel box={box} slot={slot} />
      </div>
    </section>
  )
}
