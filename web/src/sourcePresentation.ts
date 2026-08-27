export type SourcePresentation = {
  key: 'spotify' | 'zhihu' | 'douban' | 'bilibili' | 'xiaohongshu' | 'musicbrainz' | 'web'
  mark: string
  label: string
  detail: string
}

/**
 * The API keeps platform attribution as evidence data.  The browser derives
 * the visual treatment exclusively from the returned URL, so an LLM cannot
 * impersonate a platform by inventing a provider label.
 */
export function sourcePresentation(sourceUrl?: string): SourcePresentation {
  let host = ''
  try { host = new URL(sourceUrl || '').hostname.toLowerCase() } catch { /* use the safe generic variant */ }
  if (host === 'open.spotify.com' || host.endsWith('.spotify.com')) return { key: 'spotify', mark: 'S', label: 'Spotify', detail: '目录资料' }
  if (host === 'zhihu.com' || host.endsWith('.zhihu.com')) return { key: 'zhihu', mark: '知', label: '知乎', detail: host.includes('zhuanlan') ? '文章' : '讨论' }
  if (host === 'douban.com' || host.endsWith('.douban.com')) return { key: 'douban', mark: '豆', label: '豆瓣', detail: '音乐资料' }
  if (host === 'bilibili.com' || host.endsWith('.bilibili.com') || host === 'b23.tv') return { key: 'bilibili', mark: 'B', label: '哔哩哔哩', detail: '视频资料' }
  if (host === 'xiaohongshu.com' || host.endsWith('.xiaohongshu.com') || host === 'xhslink.com') return { key: 'xiaohongshu', mark: '书', label: '公开网页', detail: '社区线索' }
  if (host === 'musicbrainz.org' || host.endsWith('.musicbrainz.org')) return { key: 'musicbrainz', mark: 'MB', label: 'MusicBrainz', detail: '音乐资料库' }
  return { key: 'web', mark: '网', label: '公开网页', detail: '参考资料' }
}
