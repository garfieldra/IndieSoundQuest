import { describe, expect, it } from 'vitest'
import { progressMetricsText, publicProgress } from './agentRunStream'

describe('AgentRun public events', () => {
  it('projects structured tool completion into a visible progress item', () => {
    const value = publicProgress(
      { sequenceNumber: 4, type: 'TOOL_COMPLETED', payloadJson: '{}' },
      { toolKey: 'search_web', toolName: '网络音乐搜索', status: 'completed', message: '网络音乐搜索已完成', durationMs: 1234, metrics: { sourceCount: 8 } },
    )
    expect(value).toMatchObject({ phase: 'search_web', toolName: '网络音乐搜索', status: 'completed', elapsedMs: 1234, metrics: { sourceCount: 8 } })
    expect(progressMetricsText(value!)).toBe('来源 8 · 1.2 秒')
  })

  it('does not expose unrelated event payloads as progress', () => {
    expect(publicProgress({ sequenceNumber: 5, type: 'RESULT', payloadJson: '{}' }, {})).toBeNull()
  })
})
