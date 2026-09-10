export type AgentRunEvent = { sequenceNumber: number; type: string; payloadJson: string; createdAt?: string }
export type AgentRunPlan = { revision: number; goal: string; summary: string; changeSummary?: string; items: { id: string; title: string; status: 'pending' | 'running' | 'completed' | 'skipped' | 'blocked'; detail: string }[] }
export type AgentRunProgress = { phase: string; status?: string; message: string; elapsedMs?: number; durationMs?: number; toolName?: string; kind?: string; metrics?: Record<string, number> }
export type AgentRunTerminal<T> = { status: string; result?: T }

type Callbacks = {
  onProgress?: (value: AgentRunProgress) => void
  onPlan?: (value: AgentRunPlan) => void
  onEvent?: (event: AgentRunEvent) => void
}

const terminal = new Set(['COMPLETED', 'WAITING_FOR_USER', 'FAILED', 'CANCELLED', 'EXPIRED'])

export function publicProgress(event: AgentRunEvent, data: Record<string, unknown>): AgentRunProgress | null {
  if (!['PROGRESS', 'RETRY', 'TOOL_STARTED', 'TOOL_COMPLETED', 'TOOL_DEGRADED', 'CANCELLED'].includes(event.type)) return null
  return {
    phase: String(data.phase || data.toolKey || event.type.toLowerCase()),
    status: String(data.status || '').trim() || undefined,
    message: String(data.message || ''),
    elapsedMs: Number(data.elapsedMs || data.durationMs || 0),
    durationMs: Number(data.durationMs || 0),
    toolName: data.toolName ? String(data.toolName) : undefined,
    kind: data.kind ? String(data.kind) : undefined,
    metrics: typeof data.metrics === 'object' && data.metrics ? data.metrics as Record<string, number> : undefined,
  }
}

export function progressMetricsText(value: AgentRunProgress): string {
  const labels: Record<string, string> = { inputCount: '输入', outputCount: '返回', verifiedCount: '已核验', sourceCount: '来源', hintCount: '线索', count: '数量' }
  const values = Object.entries(value.metrics || {}).filter(([, count]) => Number.isFinite(count)).map(([key, count]) => `${labels[key] || key} ${count}`)
  if (value.durationMs && value.durationMs > 0) values.push(`${Math.max(0.1, value.durationMs / 1000).toFixed(1)} 秒`)
  return values.join(' · ')
}

export async function enqueueAgentRun(path: string, options: RequestInit): Promise<{ runId: string; eventsUrl?: string }> {
  const response = await fetch(`/api/v1${path}`, { credentials: 'include', ...options })
  if (!response.ok) throw new Error(`请求失败（${response.status}）`)
  return response.json()
}

export function followAgentRun<T>(runId: string, callbacks: Callbacks = {}, timeoutMs = 15 * 60_000, afterSequence = 0): Promise<AgentRunTerminal<T>> {
  let cursor = afterSequence
  let source: EventSource | null = null
  let settled = false
  let reconnects = 0
  let result: T | undefined
  let timeout: number | undefined

  return new Promise((resolve, reject) => {
    const finish = (value?: AgentRunTerminal<T>, error?: Error) => {
      if (settled) return
      settled = true
      source?.close()
      if (timeout) window.clearTimeout(timeout)
      if (error) reject(error); else resolve(value!)
    }
    const consume = (event: AgentRunEvent) => {
      if (event.sequenceNumber <= cursor) return
      cursor = event.sequenceNumber
      callbacks.onEvent?.(event)
      let data: Record<string, unknown> = {}
      try { data = JSON.parse(event.payloadJson) as Record<string, unknown> } catch { /* malformed public event is ignored */ }
      const progress = publicProgress(event, data)
      if (progress) callbacks.onProgress?.(progress)
      if (event.type === 'PLAN_UPDATED') callbacks.onPlan?.(data as unknown as AgentRunPlan)
      if (event.type === 'RESULT') result = data as T
      if (event.type === 'FAILED') finish(undefined, new Error(String(data.message || 'Agent 任务失败')))
    }
    const poll = async () => {
      try {
        while (!settled) {
          const response = await fetch(`/api/v1/agent-runs/${runId}/events?afterSequence=${cursor}`, { credentials: 'include' })
          if (!response.ok) throw new Error(`请求失败（${response.status}）`)
          const snapshot = await response.json() as { runStatus: string; events: AgentRunEvent[] }
          snapshot.events.forEach(consume)
          if (terminal.has(snapshot.runStatus)) {
            if (snapshot.runStatus === 'FAILED' || snapshot.runStatus === 'EXPIRED') finish(undefined, new Error('Agent 任务失败'))
            else finish({ status: snapshot.runStatus, result })
            return
          }
          await new Promise(resolveDelay => window.setTimeout(resolveDelay, 1000))
        }
      } catch (error) { finish(undefined, error instanceof Error ? error : new Error('Agent 连接失败')) }
    }
    const connect = () => {
      if (settled) return
      if (typeof EventSource === 'undefined') { void poll(); return }
      source = new EventSource(`/api/v1/agent-runs/${runId}/events:stream?afterSequence=${cursor}`, { withCredentials: true })
      source.addEventListener('run_event', raw => {
        try { consume(JSON.parse((raw as MessageEvent).data) as AgentRunEvent) } catch { /* wait for the next durable event */ }
      })
      source.addEventListener('run_status', raw => {
        const value = JSON.parse((raw as MessageEvent).data) as { status: string }
        if (!terminal.has(value.status)) return
        if (value.status === 'FAILED' || value.status === 'EXPIRED') finish(undefined, new Error('Agent 任务失败'))
        else finish({ status: value.status, result })
      })
      source.onerror = () => {
        source?.close()
        if (settled) return
        reconnects += 1
        if (reconnects <= 2) window.setTimeout(connect, reconnects * 500)
        else void poll()
      }
    }
    timeout = window.setTimeout(() => finish(undefined, new Error('Agent 任务等待超时')), timeoutMs)
    connect()
  })
}
