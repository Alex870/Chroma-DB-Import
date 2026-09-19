import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import type { JobRecord } from '../api/types'
import { OPERATION_EVENT, type OperationEvent } from './operationEvents'

export type OperationState = {
  id: string
  method: string
  label: string
  startedAt: number
  status: 'working' | 'succeeded' | 'failed'
  stage?: string
  message?: string
  percent?: number | null
  jobId?: string
}

type OperationContextValue = {
  operation: OperationState | null
  dismiss: () => void
}

const OperationContext = createContext<OperationContextValue | null>(null)

function isJob(value: unknown): value is JobRecord {
  return Boolean(value && typeof value === 'object' && 'id' in value && 'state' in value && 'stage' in value)
}

function jobProgress(job: JobRecord): Pick<OperationState, 'stage' | 'message' | 'percent'> {
  const progress = job.progress ?? {}
  return {
    stage: String(progress.stage ?? job.stage),
    message: typeof progress.message === 'string' ? progress.message : undefined,
    percent: typeof progress.percent === 'number' ? progress.percent : null,
  }
}

function terminal(job: JobRecord): boolean {
  return !['queued', 'running'].includes(job.state)
}

export function OperationProvider({ children }: { children: React.ReactNode }) {
  const [operation, setOperation] = useState<OperationState | null>(null)
  const clearTimer = useRef<number | null>(null)

  const dismiss = useCallback(() => {
    if (clearTimer.current !== null) window.clearTimeout(clearTimer.current)
    clearTimer.current = null
    setOperation(null)
  }, [])

  const scheduleSuccessClear = useCallback((id: string) => {
    if (clearTimer.current !== null) window.clearTimeout(clearTimer.current)
    clearTimer.current = window.setTimeout(() => {
      setOperation((current) => current?.id === id ? null : current)
      clearTimer.current = null
    }, 1800)
  }, [])

  useEffect(() => {
    const receive = (event: Event) => {
      const detail = (event as CustomEvent<OperationEvent>).detail
      if (!detail) return
      setOperation((current) => {
        if (detail.status === 'start') {
          if (clearTimer.current !== null) window.clearTimeout(clearTimer.current)
          clearTimer.current = null
          return { id: String(detail.operationId ?? `${Date.now()}`), method: detail.method, label: detail.label, startedAt: Date.now(), status: 'working' }
        }
        if (!current) return current
        if (detail.status === 'job' && isJob(detail.data) && current.jobId === detail.data.id) {
          const progress = jobProgress(detail.data)
          if (terminal(detail.data)) {
            const failed = ['failed', 'interrupted', 'cancelled'].includes(detail.data.state)
            const next = { ...current, ...progress, status: failed ? 'failed' as const : 'succeeded' as const, message: failed ? (detail.data.error?.message ?? 'The operation did not complete.') : (progress.message ?? 'Completed.') }
            if (!failed) scheduleSuccessClear(next.id)
            return next
          }
          return { ...current, ...progress }
        }
        if (detail.operationId && detail.operationId !== current.id) return current
        if (detail.status === 'error') return { ...current, status: 'failed', message: detail.error ?? 'The operation could not be completed.' }
        if (detail.status === 'finish') {
          if (isJob(detail.data) && !terminal(detail.data)) return { ...current, jobId: detail.data.id, ...jobProgress(detail.data), status: 'working' }
          if (isJob(detail.data)) {
            const failed = ['failed', 'interrupted', 'cancelled'].includes(detail.data.state)
            const next = { ...current, jobId: detail.data.id, ...jobProgress(detail.data), status: failed ? 'failed' as const : 'succeeded' as const, message: failed ? (detail.data.error?.message ?? 'The operation did not complete.') : 'Completed.' }
            if (!failed) scheduleSuccessClear(next.id)
            return next
          }
          const next = { ...current, status: 'succeeded' as const, message: 'Completed.' }
          scheduleSuccessClear(next.id)
          return next
        }
        return current
      })
    }
    window.addEventListener(OPERATION_EVENT, receive)
    return () => window.removeEventListener(OPERATION_EVENT, receive)
  }, [scheduleSuccessClear])

  useEffect(() => () => { if (clearTimer.current !== null) window.clearTimeout(clearTimer.current) }, [])

  const value = useMemo(() => ({ operation, dismiss }), [operation, dismiss])
  return <OperationContext.Provider value={value}>{children}</OperationContext.Provider>
}

export function useOperation(): OperationContextValue {
  const value = useContext(OperationContext)
  if (!value) throw new Error('useOperation must be used inside OperationProvider')
  return value
}

function elapsedLabel(startedAt: number, now: number): string {
  const seconds = Math.max(0, Math.floor((now - startedAt) / 1000))
  return seconds >= 60 ? `${Math.floor(seconds / 60)}m ${seconds % 60}s elapsed` : `${seconds}s elapsed`
}

function readableStage(value: string | undefined): string {
  return String(value ?? 'working').replaceAll('_', ' ').replace(/^./, (char) => char.toUpperCase())
}

export function OperationProgress({ onViewActivity }: { onViewActivity?: () => void }) {
  const { operation, dismiss } = useOperation()
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    if (!operation || operation.status !== 'working') return
    const id = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(id)
  }, [operation])

  if (!operation) return null
  const running = operation.status === 'working'
  const percent = typeof operation.percent === 'number' ? Math.max(0, Math.min(100, operation.percent)) : null
  return <section className={`operation-progress operation-${operation.status}`} role="status" aria-live="polite">
    {running ? <span className="spinner" aria-hidden="true" /> : <span className="operation-check" aria-hidden="true">{operation.status === 'succeeded' ? '✓' : '!'}</span>}
    <div className="operation-copy"><strong>{running ? operation.label : operation.status === 'succeeded' ? `${operation.label} complete` : operation.label}</strong><span>{operation.message || readableStage(operation.stage)}{running && (now - operation.startedAt >= 5000) ? ` · ${elapsedLabel(operation.startedAt, now)}` : ''}</span>{percent !== null && <div className="operation-meter" aria-label={`${percent}% complete`}><span style={{ width: `${percent}%` }} /></div>}</div>
    {percent !== null && <strong className="operation-percent">{Math.round(percent)}%</strong>}
    {onViewActivity && operation.jobId && <button className="button small" onClick={onViewActivity}>View activity</button>}
    {!running && <button className="icon-button" aria-label="Dismiss operation status" onClick={dismiss}>×</button>}
  </section>
}
