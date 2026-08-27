import { describe, expect, it } from 'vitest'
import { sourcePresentation } from './sourcePresentation'

describe('sourcePresentation', () => {
  it.each([
    ['https://open.spotify.com/track/abc', 'spotify', 'Spotify'],
    ['https://www.zhihu.com/question/1/answer/2', 'zhihu', '知乎'],
    ['https://music.douban.com/subject/123', 'douban', '豆瓣'],
    ['https://www.bilibili.com/video/BV1xx', 'bilibili', '哔哩哔哩'],
    ['https://www.xiaohongshu.com/explore/abc', 'xiaohongshu', '公开网页'],
    ['https://musicbrainz.org/recording/abc', 'musicbrainz', 'MusicBrainz'],
    ['https://example.org/music', 'web', '公开网页'],
  ])('maps %s to the expected source card', (url, key, label) => {
    expect(sourcePresentation(url).key).toBe(key)
    expect(sourcePresentation(url).label).toBe(label)
  })

  it('safely falls back for malformed URLs', () => {
    expect(sourcePresentation('not a url').key).toBe('web')
  })
})
