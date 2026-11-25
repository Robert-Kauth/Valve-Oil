import type { ReactNode } from 'react'
import { errorText } from '../lib/browser'
import { t } from '../i18n'
import { isActive } from '../state/job'
import { client, printer, runGcode } from '../state/printer'
import { attempt, toast } from './toast'
import { confirm } from './ui'

type SaveConfigPrompt = {
  title: ReactNode
  body: ReactNode
  confirm: string
}

type SaveConfigResult = 'cancelled' | 'blocked' | 'sent' | 'failed'
type LivePrintState = 'idle' | 'running'
enum SaveConfigStateFailure {
  RequestFailed = 'request-failed',
  MalformedResponse = 'malformed-response',
}

class SaveConfigStateError extends Error {
  constructor(readonly reason: SaveConfigStateFailure, cause?: unknown) {
    super(reason === SaveConfigStateFailure.RequestFailed ? errorText(cause) : t('system.diagnostics.check.unreadable'), { cause })
    this.name = 'SaveConfigStateError'
  }
}

const record = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value)

async function readLivePrintState(): Promise<LivePrintState> {
  let answer: unknown
  try {
    answer = await client.call('printer.objects.query', { objects: { print_stats: ['state'] } })
  } catch (error) {
    throw new SaveConfigStateError(SaveConfigStateFailure.RequestFailed, error)
  }

  if (!record(answer) || !record(answer.status) || !record(answer.status.print_stats)) throw new SaveConfigStateError(SaveConfigStateFailure.MalformedResponse)
  switch (answer.status.print_stats.state) {
    case 'printing':
    case 'paused':
      return 'running'
    case 'standby':
    case 'complete':
    case 'cancelled':
    case 'error':
      return 'idle'
    default:
      throw new SaveConfigStateError(SaveConfigStateFailure.MalformedResponse)
  }
}

/** Confirms and dispatches SAVE_CONFIG only after a fresh validated idle-state read. */
export async function confirmAndSaveConfig(prompt: SaveConfigPrompt): Promise<SaveConfigResult> {
  const confirmed = await confirm({
    ...prompt,
    validate: () => (isActive(printer.get().status.print_stats?.state) ? t('app.save_config.waits') : null),
  })
  if (!confirmed) return 'cancelled'

  const result = await attempt(async (): Promise<Exclude<SaveConfigResult, 'cancelled' | 'failed'>> => {
    if ((await readLivePrintState()) === 'running') {
      toast(t('app.save_config.waits'), 'error')
      return 'blocked'
    }
    await runGcode('SAVE_CONFIG')
    return 'sent'
  })
  return result ?? 'failed'
}
