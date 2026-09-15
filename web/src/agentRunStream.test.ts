import { describe, expect, it } from 'vitest'
import { progressMetricsText, projectResponseStream, publicProgress } from './agentRunStream'

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

  it('projects cancellation into a visible terminal message', () => {
    expect(publicProgress(
      { sequenceNumber: 6, type: 'CANCELLED', payloadJson: '{}' },
      { phase: 'cancelled', message: '已停止本轮任务' },
    )).toMatchObject({ phase: 'cancelled', message: '已停止本轮任务' })
  })

  it('projects a runtime direction adjustment without exposing hidden reasoning', () => {
    expect(publicProgress(
      { sequenceNumber: 7, type: 'INTERVENTION_APPLIED', payloadJson: '{}' },
      { phase: 'direction_update', status: 'applied', message: '正在根据你的补充调整计划', interventionSequence: 2 },
    )).toMatchObject({ phase: 'direction_update', status: 'applied', message: '正在根据你的补充调整计划' })
  })

  it('projects safe commentary and reconstructs durable response deltas', () => {
    expect(publicProgress(
      { sequenceNumber: 8, type: 'COMMENTARY', payloadJson: '{}' },
      { phase: 'search_web', status: 'commentary', message: '我已经找到可用线索。' },
    )).toMatchObject({ status: 'commentary', message: '我已经找到可用线索。' })
    expect(projectResponseStream('旧内容', { sequenceNumber: 9, type: 'RESPONSE_STARTED', payloadJson: '{}' }, {})).toBe('')
    expect(projectResponseStream('第一段', { sequenceNumber: 10, type: 'RESPONSE_DELTA', payloadJson: '{}' }, { delta: '，第二段' })).toBe('第一段，第二段')
  })
})
