import { describe, expect, it, vi } from 'vitest'
import { ClientError } from './client'

describe('modern bridge contracts', () => {
  it('uses a stable connection error when the privileged bridge is absent', async () => {
    vi.useFakeTimers()
    try {
      const { client } = await import('./client')
      const pending = client.listDatabases().catch((error: unknown) => error)
      await vi.advanceTimersByTimeAsync(5000)
      const error = await pending
      expect(error).toBeInstanceOf(ClientError)
      expect(error).toMatchObject({ detail: { code: 'BRIDGE_UNAVAILABLE' } })
    } finally {
      vi.useRealTimers()
    }
  })
})
