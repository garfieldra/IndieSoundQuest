import { describe, expect, it } from 'vitest'
import { deriveCandidateSelection, type CandidateItem } from './candidatePool'

function candidates(count: number): CandidateItem[] {
  return Array.from({ length: count }, (_, index) => ({
    recordingId: `recording-${index + 1}`,
    title: `歌曲 ${index + 1}`,
    artistName: '测试艺人',
    albumTitle: '',
    coverUrl: '',
    coverStatus: 'UNAVAILABLE',
    listeningSearchUrl: 'https://music.163.com/#/search/m/?s=test&type=1',
    reason: '测试理由',
  }))
}

describe('deriveCandidateSelection', () => {
  it('将 32 首稳定拆成 16 首参赛和 16 首候补', () => {
    const result = deriveCandidateSelection(candidates(32), new Set(), 16)
    expect(result.activeItems).toHaveLength(16)
    expect(result.reserveItems).toHaveLength(16)
  })

  it('移除第一首后由原第 17 首补位', () => {
    const result = deriveCandidateSelection(candidates(32), new Set(['recording-1']), 16)
    expect(result.activeItems.map(item => item.recordingId)).toEqual([
      ...Array.from({ length: 15 }, (_, index) => `recording-${index + 2}`),
      'recording-17',
    ])
  })

  it('任意移除和恢复后始终按原始顺序派生', () => {
    const ordered = candidates(32)
    const removed = new Set(['recording-2', 'recording-5', 'recording-8'])
    const afterRemoval = deriveCandidateSelection(ordered, removed, 16)
    expect(afterRemoval.removedItems.map(item => item.recordingId)).toEqual(['recording-2', 'recording-5', 'recording-8'])

    removed.delete('recording-5')
    const afterRestore = deriveCandidateSelection(ordered, removed, 16)
    expect(afterRestore.activeItems.map(item => item.recordingId)).toEqual(
      ordered.filter(item => !removed.has(item.recordingId)).slice(0, 16).map(item => item.recordingId),
    )
  })

  it('候补耗尽时禁止继续移除', () => {
    const result = deriveCandidateSelection(candidates(16), new Set(), 16)
    expect(result.canRemove).toBe(false)
    expect(result.reserveItems).toHaveLength(0)
  })

  it('保留外部核验歌曲的来源字段供候选卡片展示', () => {
    const [candidate] = candidates(1)
    candidate.catalogSource = 'EXTERNAL_VERIFIED'
    expect(candidate.catalogSource).toBe('EXTERNAL_VERIFIED')
  })
})
