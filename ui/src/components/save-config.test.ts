import { beforeEach, expect, it, vi } from 'vitest'

type ConfirmPrompt = { validate?: () => string | null }

const fakes = vi.hoisted(() => ({
  cachedState: 'standby',
  confirm: vi.fn<(prompt: ConfirmPrompt) => Promise<boolean>>(),
  read: vi.fn<(method: string, params: Record<string, unknown>) => Promise<unknown>>(),
  send: vi.fn<() => Promise<void>>(),
  toast: vi.fn<(message: string, tone?: 'error' | 'ok') => void>(),
}))

vi.mock('./ui', () => ({ confirm: fakes.confirm }))
vi.mock('../state/printer', () => ({
  client: { call: fakes.read },
  printer: { get: () => ({ status: { print_stats: { state: fakes.cachedState } } }) },
  runGcode: fakes.send,
}))
vi.mock('./toast', () => ({
  toast: fakes.toast,
  attempt: async (action: () => Promise<unknown>) => {
    try {
      return await action()
    } catch (error) {
      fakes.toast(error instanceof Error ? error.message : String(error), 'error')
      return undefined
    }
  },
}))
vi.mock('../i18n', () => ({
  t: (key: string) => (key === 'app.save_config.waits' ? 'wait for the print' : key === 'system.diagnostics.check.unreadable' ? 'unreadable printer response' : key),
}))

const prompt = { title: 'Save?', body: 'Restart Klipper', confirm: 'Save' }
const status = (state: string) => ({ status: { print_stats: { state } } })

beforeEach(() => {
  fakes.cachedState = 'standby'
  fakes.confirm.mockReset().mockResolvedValue(true)
  fakes.read.mockReset()
  fakes.send.mockReset().mockResolvedValue()
  fakes.toast.mockReset()
})

it('blocks when the printer starts running after confirmation', async () => {
  fakes.read.mockResolvedValue(status('printing'))
  const { confirmAndSaveConfig } = await import('./save-config')

  await expect(confirmAndSaveConfig(prompt)).resolves.toBe('blocked')
  expect(fakes.read).toHaveBeenCalledWith('printer.objects.query', { objects: { print_stats: ['state'] } })
  expect(fakes.send).not.toHaveBeenCalled()
  expect(fakes.toast).toHaveBeenCalledWith('wait for the print', 'error')
})

it('sends SAVE_CONFIG exactly once after confirmation and a final idle read', async () => {
  fakes.read.mockResolvedValue(status('standby'))
  const { confirmAndSaveConfig } = await import('./save-config')

  await expect(confirmAndSaveConfig(prompt)).resolves.toBe('sent')
  expect(fakes.read).toHaveBeenCalledWith('printer.objects.query', { objects: { print_stats: ['state'] } })
  expect(fakes.send).toHaveBeenCalledOnce()
  expect(fakes.send).toHaveBeenCalledWith('SAVE_CONFIG')
})

it('does not read or send when confirmation is cancelled', async () => {
  fakes.confirm.mockResolvedValue(false)
  const { confirmAndSaveConfig } = await import('./save-config')

  await expect(confirmAndSaveConfig(prompt)).resolves.toBe('cancelled')
  expect(fakes.read).not.toHaveBeenCalled()
  expect(fakes.send).not.toHaveBeenCalled()
})

it('keeps the confirmation open with waiting feedback when cached state becomes active', async () => {
  fakes.cachedState = 'paused'
  fakes.confirm.mockImplementation(async (asked: ConfirmPrompt) => {
    expect(asked.validate?.()).toBe('wait for the print')
    return false
  })
  const { confirmAndSaveConfig } = await import('./save-config')

  await expect(confirmAndSaveConfig(prompt)).resolves.toBe('cancelled')
  expect(fakes.read).not.toHaveBeenCalled()
  expect(fakes.send).not.toHaveBeenCalled()
})

it('fails closed when the final state read fails', async () => {
  fakes.read.mockRejectedValue(new Error('Moonraker disconnected'))
  const { confirmAndSaveConfig } = await import('./save-config')

  await expect(confirmAndSaveConfig(prompt)).resolves.toBe('failed')
  expect(fakes.send).not.toHaveBeenCalled()
  expect(fakes.toast).toHaveBeenCalledWith('Moonraker disconnected', 'error')
})

it.each([
  {},
  { status: {} },
  { status: { print_stats: {} } },
  status('unexpected'),
])('fails closed on malformed final state %#', async (answer: unknown) => {
  fakes.read.mockResolvedValue(answer)
  const { confirmAndSaveConfig } = await import('./save-config')

  await expect(confirmAndSaveConfig(prompt)).resolves.toBe('failed')
  expect(fakes.send).not.toHaveBeenCalled()
  expect(fakes.toast).toHaveBeenCalledWith('unreadable printer response', 'error')
})
