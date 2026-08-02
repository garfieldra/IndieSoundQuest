import { useEffect, useMemo, useState } from 'react'

type Artist = { id: string; name: string }
type Entry = { id: string; title: string; artistName: string; albumTitle?: string; coverUrl?: string; coverStatus: string }
type Match = { id: string; roundNumber: number; matchIndex: number; leftEntryId: string | null; rightEntryId: string | null; winnerEntryId: string | null; status: string }
type Tournament = { id: string; status: string; size: number; completedVoteCount: number; entries: Entry[]; matches: Match[]; currentMatch: Match | null }

async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api/v1${path}`, { credentials: 'include', ...options })
  if (!response.ok) throw new Error(`请求失败（${response.status}）`)
  return response.json() as Promise<T>
}

export function App() {
  const [artists, setArtists] = useState<Artist[]>([])
  const [artistId, setArtistId] = useState('')
  const [size, setSize] = useState<16 | 32>(16)
  const [tournament, setTournament] = useState<Tournament | null>(null)
  const [loading, setLoading] = useState(true)
  const [message, setMessage] = useState('')

  useEffect(() => { void loadArtists() }, [])
  async function loadArtists() {
    try {
      const data = await api<Artist[]>('/artists')
      setArtists(data); setArtistId(data[0]?.id ?? '')
    } catch { setMessage('暂时无法连接歌曲服务，请确认 Java 服务已启动。') }
    finally { setLoading(false) }
  }
  async function refresh(id = tournament?.id) {
    if (!id) return
    setTournament(await api<Tournament>(`/tournaments/${id}`))
  }
  async function createTournament() {
    if (!artistId) return
    setLoading(true); setMessage('')
    try {
      const created = await api<{ id: string }>('/tournaments', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ artistId, size }) })
      await api(`/tournaments/${created.id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status: 'PREPARED' }) })
      await refresh(created.id)
    } catch (error) { setMessage(error instanceof Error ? error.message : '创建赛事失败') }
    finally { setLoading(false) }
  }
  async function vote(entryId: string) {
    if (!tournament?.currentMatch) return
    setLoading(true); setMessage('')
    try {
      await api(`/tournament-matches/${tournament.currentMatch.id}/votes`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID() }, body: JSON.stringify({ selectedEntryId: entryId }) })
      await refresh()
    } catch (error) { setMessage(error instanceof Error ? error.message : '投票失败') }
    finally { setLoading(false) }
  }

  const entryById = useMemo(() => new Map(tournament?.entries.map(entry => [entry.id, entry])), [tournament])
  const current = tournament?.currentMatch
  const left = current?.leftEntryId ? entryById.get(current.leftEntryId) : undefined
  const right = current?.rightEntryId ? entryById.get(current.rightEntryId) : undefined
  return <main className="app-shell">
    <header><p className="eyebrow">IndieSoundQuest</p><h1>把喜欢，投进一场歌的世界杯。</h1><p className="subtitle">一对一选择，最后留下真正属于你的冠军歌曲。</p></header>
    {message && <p className="notice">{message}</p>}
    {!tournament && <section className="panel setup"><h2>创建一场赛事</h2><label>艺人<select value={artistId} onChange={event => setArtistId(event.target.value)}>{artists.map(artist => <option key={artist.id} value={artist.id}>{artist.name}</option>)}</select></label><fieldset><legend>候选歌曲</legend><button className={size === 16 ? 'selected' : ''} onClick={() => setSize(16)}>16 首 · 快速体验</button><button className={size === 32 ? 'selected' : ''} onClick={() => setSize(32)}>32 首 · 完整世界杯</button></fieldset><button className="primary" disabled={loading || !artistId} onClick={createTournament}>{loading ? '正在生成赛程…' : '开始这场比赛'}</button></section>}
    {tournament && <section className="panel arena"><div className="progress"><span>{tournament.status === 'COMPLETED' ? '赛事结束' : `第 ${tournament.completedVoteCount + 1} 场选择`}</span><span>{tournament.completedVoteCount} / {tournament.size - 1}</span></div>{current && left && right ? <><h2>这一轮，你更想留下谁？</h2><div className="matchup"><SongCard entry={left} onClick={() => void vote(left.id)} disabled={loading}/><div className="versus">VS</div><SongCard entry={right} onClick={() => void vote(right.id)} disabled={loading}/></div></> : <><h2>冠军诞生</h2><p>你已完成 {tournament.completedVoteCount} 场选择。这场比赛的结果已保存到你的访客会话中。</p><button className="primary" onClick={() => setTournament(null)}>再办一场</button></>}</section>}
  </main>
}

function SongCard({ entry, onClick, disabled }: { entry: Entry; onClick: () => void; disabled: boolean }) {
  return <button className="song-card" onClick={onClick} disabled={disabled}><img src={entry.coverUrl || ''} alt={`${entry.albumTitle ?? entry.title} 封面`} /><span>{entry.title}</span><small>{entry.artistName}</small></button>
}
