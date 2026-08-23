import { describe, expect, it } from 'vitest'
import { playbackLabel } from './listening'

describe('playbackLabel', () => {
  it('keeps playback and voting language separate', () => {
    expect(playbackLabel('idle')).toBe('点击卡片试听')
    expect(playbackLabel('playing')).toContain('点击暂停')
    expect(playbackLabel('unavailable')).toBe('暂无试听')
  })
})

