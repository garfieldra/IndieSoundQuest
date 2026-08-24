import { type ReactNode, type RefObject, useEffect, useMemo, useRef, useState } from 'react'
import { toPng } from 'html-to-image'
import { deriveCandidateSelection, intentModeLabel, type ArtistChoice, type CandidateItem, type CandidatePoolResponse } from './candidatePool'
import { playbackLabel, type PlaybackState, useListeningPreview } from './listening'
import { TournamentResultView } from './tournamentResultView'
import './report.css'
import './discovery.css'
import './export.css'
import './listening.css'
import './evidence.css'
import './agentProgress.css'
import './agentPlan.css'

type Artist = { id: string; name: string }
type Entry = { id: string; recordingId: string; title: string; artistName: string; albumTitle?: string; coverUrl?: string; coverStatus: string; listeningSearchUrl: string }
type Match = { id: string; roundNumber: number; matchIndex: number; leftEntryId: string | null; rightEntryId: string | null; winnerEntryId: string | null; status: string }
type Tournament = { id: string; status: string; size: number; completedVoteCount: number; completedAt?: string | null; entries: Entry[]; matches: Match[]; currentMatch: Match | null }
type Report = { reportId: string; tournamentId: string; version: number; status: 'PENDING' | 'RUNNING' | 'READY' | 'FAILED'; report?: { summary: string; dimensions: { name: string; confidence: string; explanation: string }[]; choiceTrajectory?: { matchId: string; roundNumber: number; matchIndex: number; winnerTitle: string; winnerArtistName: string; loserTitle: string; loserArtistName: string; signalRole: 'stable_anchor' | 'preference_boundary' | 'near_finalist'; derivedNote: string }[]; songRecommendations: { recordingId?: string; title?: string; artistName?: string; reason: string; searchUrl?: string; sourceStatus?: 'catalog_verified' | 'web_discovered'; sourceUrl?: string; sourceTitle?: string }[]; artistRecommendations: { artistId?: string; artistName?: string; reason: string; searchUrl?: string; sourceStatus?: 'catalog_verified' | 'web_discovered'; sourceUrl?: string; sourceTitle?: string }[]; explorationTags?: string[]; personalityEasterEgg: string; disclaimer: string; warnings?: string[] }; failureMessage?: string }
type AgentProgress = { phase: string; status?: string; message: string; elapsedMs?: number }
type AgentPlan = { revision: number; goal: string; summary: string; items: { id: string; title: string; status: 'pending' | 'running' | 'completed' | 'skipped' | 'blocked'; detail: string }[] }

function agentErrorMessage(error: unknown, fallback: string) {
  const message = error instanceof Error ? error.message.trim() : ''
  if (/^(load failed|failed to fetch|networkerror)$/i.test(message)) return '探索连接意外中断了，请检查网络后重试。已完成的赛事不会受影响。'
  return message || fallback
}

async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api/v1${path}`, { credentials: 'include', ...options })
  if (!response.ok) throw new Error(`请求失败（${response.status}）`)
  return response.json() as Promise<T>
}

async function streamAgent<T>(path: string, options: RequestInit, onProgress: (progress: AgentProgress) => void, onPlan: (plan: AgentPlan) => void): Promise<T> {
  const response = await fetch(`/api/v1${path}`, { credentials: 'include', ...options })
  if (!response.ok || !response.body) throw new Error(`请求失败（${response.status}）`)
  const reader = response.body.getReader(); const decoder = new TextDecoder(); let pending = ''; let result: T | undefined
  const consume = (block: string) => {
    const event = block.match(/^event:\s*(.+)$/m)?.[1]?.trim()
    const data = block.match(/^data:\s*(.+)$/m)?.[1]?.trim()
    if (!event || !data) return
    if (event === 'progress') onProgress(JSON.parse(data) as AgentProgress)
    else if (event === 'plan_updated') onPlan(JSON.parse(data) as AgentPlan)
    else if (event === 'result') result = JSON.parse(data) as T
    else if (event === 'error') { const issue = JSON.parse(data) as { message?: string }; throw new Error(issue.message || 'Agent 服务暂时不可用') }
  }
  while (true) { const { value, done } = await reader.read(); pending += decoder.decode(value ?? new Uint8Array(), { stream: !done }); let boundary: number
    while ((boundary = pending.indexOf('\n\n')) >= 0) { consume(pending.slice(0, boundary)); pending = pending.slice(boundary + 2) }
    if (done) break
  }
  if (!result) throw new Error('Agent 未返回最终结果')
  return result
}

export function App() {
  const conversationLaunch = sessionStorage.getItem('isq-worldcup-preference')
  const conversationLaunchSize = sessionStorage.getItem('isq-worldcup-size') === '32' ? 32 : 16
  const conversationId = sessionStorage.getItem('isq-conversation-id')
  const existingTournamentId = sessionStorage.getItem('isq-open-tournament-id')
  const [artists, setArtists] = useState<Artist[]>([])
  const [artistId, setArtistId] = useState('')
  const [seedArtistId, setSeedArtistId] = useState('')
  const [size, setSize] = useState<16 | 32>(conversationLaunchSize)
  const [tournament, setTournament] = useState<Tournament | null>(null)
  const [loading, setLoading] = useState(true)
  const [message, setMessage] = useState('')
  const [mode, setMode] = useState<'popular' | 'explore'>('explore')
  const [preferenceText, setPreferenceText] = useState(conversationLaunch ?? '')
  const [candidateResult, setCandidateResult] = useState<CandidatePoolResponse | null>(null)
  const [confirmedArtists, setConfirmedArtists] = useState<{ mention: string; mbid: string; name: string }[]>([])
  const [excludedIds, setExcludedIds] = useState<string[]>([])
  const [preparingTournamentId, setPreparingTournamentId] = useState<string | null>(null)
  const [report, setReport] = useState<Report | null>(null)
  const [reportLoading, setReportLoading] = useState(false)
  const [agentProgress, setAgentProgress] = useState<AgentProgress[]>([])
  const [agentPlan, setAgentPlan] = useState<AgentPlan | null>(null)
  const [progressCollapsed, setProgressCollapsed] = useState(false)
  const [agentRunActive, setAgentRunActive] = useState(false)
  const listening = useListeningPreview()
  const creationAttempt = useRef<{ signature: string; key: string } | null>(null)
  const launchPending = useRef(Boolean(conversationLaunch))

  function creationKeyFor(payload: object) {
    const signature = JSON.stringify(payload)
    if (creationAttempt.current?.signature !== signature) creationAttempt.current = { signature, key: crypto.randomUUID() }
    return creationAttempt.current.key
  }
  async function persistConversationCard(type: 'CANDIDATE_POOL_CARD' | 'TOURNAMENT_CARD' | 'REPORT_CARD', cardType: string, payload: object) {
    if (!conversationId) return
    await api(`/conversations/${conversationId}/cards`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID() }, body: JSON.stringify({ type, cardType, payloadJson: JSON.stringify(payload) }) })
  }

  useEffect(() => { void loadArtists() }, [])
  useEffect(() => { if (!loading && existingTournamentId) { sessionStorage.removeItem('isq-open-tournament-id'); void refresh(existingTournamentId) } }, [loading])
  useEffect(() => {
    if (!loading && launchPending.current && preferenceText.trim().length >= 3) {
      launchPending.current = false
      sessionStorage.removeItem('isq-worldcup-preference'); sessionStorage.removeItem('isq-worldcup-size')
      void generateCandidates()
    }
  }, [loading])
  async function loadArtists() {
    try {
      const data = await api<Artist[]>('/artists')
      setArtists(data); setArtistId(data[0]?.id ?? '')
    } catch { setMessage('暂时无法连接歌曲服务，请确认 Java 服务已启动。') }
    finally { setLoading(false) }
  }
  async function refresh(id = tournament?.id) {
    if (!id) return
    const detail = await api<Tournament>(`/tournaments/${id}`)
    setTournament(detail)
    if (detail.status === 'COMPLETED') {
      try { setReport(await api<Report>(`/tournaments/${id}/preference-report`)) }
      catch { setReport(null) }
    }
  }
  async function createTournament() {
    if (!artistId) return
    setLoading(true); setMessage('')
    try {
      const payload = { artistId, size }
      const created = await api<{ id: string; status: string }>('/tournaments', { method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': creationKeyFor(payload) }, body: JSON.stringify(payload) })
      if (created.status === 'DRAFT') await api(`/tournaments/${created.id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status: 'READY' }) })
      await refresh(created.id)
      creationAttempt.current = null
    } catch (error) { setMessage(error instanceof Error ? error.message : '创建赛事失败') }
    finally { setLoading(false) }
  }
  async function generateCandidates(nextConfirmedArtists = confirmedArtists) {
    if (preferenceText.trim().length < 3) { setMessage('请用一句话描述你想探索的音乐。'); return }
    setLoading(true); setAgentRunActive(true); setMessage(''); setAgentProgress([]); setAgentPlan(null); setProgressCollapsed(false)
    try {
      const requestId = crypto.randomUUID()
      const data = await streamAgent<CandidatePoolResponse>('/agent-runs/candidate-pool:stream', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Request-Id': requestId }, body: JSON.stringify({ size, preferenceText, seedArtistIds: seedArtistId ? [seedArtistId] : [], confirmedArtists: nextConfirmedArtists }) }, item => setAgentProgress(current => [...current, item]), setAgentPlan)
      setCandidateResult(data); setExcludedIds([]); setPreparingTournamentId(null); creationAttempt.current = null
      if (conversationId) await persistConversationCard('CANDIDATE_POOL_CARD', 'CANDIDATE_POOL', { size, status: data.status, preferenceText, summary: data.candidatePool?.candidateSummary || '', recordingIds: data.candidatePool?.items.map(item => item.recordingId) || [] })
      if (data.status === 'insufficient_candidates') setMessage(data.candidatePool?.warnings[0]?.message || '可验证歌曲不足，请调整兴趣方向。')
    }
    catch (error) { setMessage(agentErrorMessage(error, '候选歌曲暂时无法生成')) } finally { setLoading(false); setAgentRunActive(false); setProgressCollapsed(true) }
  }
  function changeSize(nextSize: 16 | 32) {
    if (nextSize === size) return
    setSize(nextSize)
    if (candidateResult) {
      setCandidateResult(null); setExcludedIds([]); setPreparingTournamentId(null); creationAttempt.current = null
      setMessage('赛事规模已改变，请按新的规模重新生成候选歌曲。')
    }
  }
  function removeCandidate(id: string) {
    if (!candidateSelection.canRemove || !candidateSelection.activeItems.some(item => item.recordingId === id)) return
    setExcludedIds(current => current.includes(id) ? current : [...current, id])
  }
  function restoreCandidate(id: string) { setExcludedIds(current => current.filter(candidateId => candidateId !== id)) }
  async function prepareExploration(tournamentId: string) {
    setLoading(true); setMessage('')
    try {
      await api(`/tournaments/${tournamentId}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status: 'READY' }) })
      await refresh(tournamentId); setPreparingTournamentId(null); creationAttempt.current = null
    } catch { setMessage('赛事已经创建，但赛程准备尚未完成。你可以直接重试进入赛事。') }
    finally { setLoading(false) }
  }
  async function startExploration() {
    if (!candidateResult || candidateResult.status !== 'ready_for_confirmation') return
    if (preparingTournamentId) { await prepareExploration(preparingTournamentId); return }
    setLoading(true); setMessage('')
    try {
      const payload = { size, candidateSource: 'AGENT_GENERATED', recordingIds: candidateSelection.activeItems.map(item => item.recordingId), explorationBrief: preferenceText }
      const created = await api<{ id: string; status: string }>('/tournaments', { method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': creationKeyFor(payload) }, body: JSON.stringify(payload) })
      setPreparingTournamentId(created.id)
      if (created.status === 'DRAFT') await api(`/tournaments/${created.id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status: 'READY' }) })
      await refresh(created.id); setPreparingTournamentId(null); creationAttempt.current = null
      await persistConversationCard('TOURNAMENT_CARD', 'TOURNAMENT', { tournamentId: created.id, size, status: 'READY' })
    } catch { setMessage(preparingTournamentId ? '赛程准备失败，请重试进入赛事。' : '创建结果暂不确定；再次点击会使用同一请求安全重试。') }
    finally { setLoading(false) }
  }
  async function vote(entryId: string) {
    if (!tournament?.currentMatch) return
    listening.stop()
    setLoading(true); setMessage('')
    try {
      await api(`/tournament-matches/${tournament.currentMatch.id}/votes`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID() }, body: JSON.stringify({ selectedEntryId: entryId }) })
      await refresh()
    } catch (error) { setMessage(error instanceof Error ? error.message : '投票失败') }
    finally { setLoading(false) }
  }
  async function generateReport(force = false) {
    if (!tournament) return
    setReportLoading(true); setAgentRunActive(true); setMessage(''); setAgentProgress([]); setAgentPlan(null); setProgressCollapsed(false)
    try {
      const created = await streamAgent<Report>(`/tournaments/${tournament.id}/preference-report:stream`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ force }) }, item => setAgentProgress(current => [...current, item]), setAgentPlan)
      setReport(created)
      await persistConversationCard('REPORT_CARD', 'PREFERENCE_REPORT', { tournamentId: tournament.id, reportId: created.reportId, version: created.version, status: created.status, championTitle: tournament.entries.find(entry => entry.id === tournament.matches.find(match => match.roundNumber === Math.max(...tournament.matches.map(item => item.roundNumber)) && match.winnerEntryId)?.winnerEntryId)?.title || '' })
    } catch (error) { setMessage(agentErrorMessage(error, '报告暂时无法生成')) }
    finally { setReportLoading(false); setAgentRunActive(false); setProgressCollapsed(true) }
  }

  const entryById = useMemo(() => new Map(tournament?.entries.map(entry => [entry.id, entry])), [tournament])
  const candidatePool = candidateResult?.candidatePool
  const candidateSelection = useMemo(
    () => deriveCandidateSelection(candidatePool?.items ?? [], new Set(excludedIds), size),
    [candidatePool, excludedIds, size],
  )
  const current = tournament?.currentMatch
  const left = current?.leftEntryId ? entryById.get(current.leftEntryId) : undefined
  const right = current?.rightEntryId ? entryById.get(current.rightEntryId) : undefined
  const agentFeedback = agentRunActive && (agentPlan || agentProgress.length > 0)
    ? <AgentRunFeedback plan={agentPlan} items={agentProgress} collapsed={progressCollapsed} onToggle={() => setProgressCollapsed(value => !value)} />
    : null
  return <main className="app-shell">
    <header><p className="eyebrow">IndieSoundQuest</p><h1>把喜欢，投进一场歌的世界杯。</h1><p className="subtitle">一对一选择，最后留下真正属于你的冠军歌曲。</p></header>
    {message && <p className="notice">{message}</p>}
    {!tournament && <section className="panel setup">
      <div className="mode-tabs">
        <button className={mode === 'explore' ? 'selected' : ''} onClick={() => setMode('explore')}>按偏好探索</button>
        <button className={mode === 'popular' ? 'selected' : ''} onClick={() => setMode('popular')}>代表作直接开赛</button>
      </div>
      {mode === 'popular' ? <>
        <h2>创建一场赛事</h2>
        <label>艺人<select value={artistId} onChange={event => setArtistId(event.target.value)}>{artists.map(artist => <option key={artist.id} value={artist.id}>{artist.name}</option>)}</select></label>
        <fieldset><legend>候选歌曲</legend><button className={size === 16 ? 'selected' : ''} onClick={() => changeSize(16)}>16 首</button><button className={size === 32 ? 'selected' : ''} onClick={() => changeSize(32)}>32 首</button></fieldset>
        <button className="primary" disabled={loading || !artistId} onClick={createTournament}>开始这场比赛</button>
      </> : !candidateResult ? <>
        <h2>把你的偏好写下来</h2>
        <label>从哪一种声音开始（描述你的音乐喜好，比如喜欢哪位歌手、哪张专辑、哪种风格）<textarea value={preferenceText} onChange={e => setPreferenceText(e.target.value)} placeholder="例如：克制、有叙事感的中文独立音乐，适合夜晚散步。" /></label>
        <label>起点艺人（可选）<select value={seedArtistId} onChange={event => setSeedArtistId(event.target.value)}><option value="">不限定，从文字偏好开始</option>{artists.map(artist => <option key={artist.id} value={artist.id}>{artist.name}</option>)}</select></label>
        <fieldset><legend>赛事规模</legend><button className={size === 16 ? 'selected' : ''} onClick={() => changeSize(16)}>16 首</button><button className={size === 32 ? 'selected' : ''} onClick={() => changeSize(32)}>32 首</button></fieldset>
        <button className="primary" disabled={loading} onClick={() => void generateCandidates()}>{loading ? '正在寻找适合这场比赛的歌曲…' : '生成候选歌曲'}</button>
        {agentFeedback}
      </> : candidateResult.status === 'needs_clarification' ? <ArtistClarificationView clarifications={candidateResult.clarifications ?? []} loading={loading} feedback={agentFeedback} onConfirm={(choices) => { setConfirmedArtists(choices); void generateCandidates(choices) }} onRestart={() => { setCandidateResult(null); setConfirmedArtists([]); setMessage('') }} /> : candidateResult.status === 'insufficient_candidates' ? <section className="candidate-state" aria-live="polite">
        <p className="eyebrow">候选数量不足</p>
        <h2>这次还不足以组成一场比赛</h2>
        <p className="subtitle">{candidatePool?.candidateSummary}</p>
        {candidatePool?.warnings.map(warning => <p className="candidate-warning" key={warning.code}>{warning.message}</p>)}
        <div className="candidate-actions"><button className="secondary" onClick={() => { setCandidateResult(null); setMessage('') }}>修改兴趣方向</button><button className="primary" disabled={loading} onClick={() => void generateCandidates()}>重新生成</button></div>{agentFeedback}
      </section> : <section className="candidate-shelf">
        <p className="eyebrow">候选唱片架</p>
        <h2>从这一组声音开始</h2>
        {candidatePool?.intentMode && <p className="intent-mode">系统理解：{intentModeLabel[candidatePool.intentMode]}</p>}
        <p className="subtitle">{candidatePool?.candidateSummary}</p>
        <div className="candidate-counts" aria-live="polite"><span>当前参赛 {candidateSelection.activeItems.length} 首</span><span>剩余候补 {candidateSelection.reserveItems.length} 首</span></div>
        {candidatePool?.warnings.map(warning => <p className="candidate-warning" key={warning.code}>{warning.message}</p>)}
        <div className="candidate-grid">{candidateSelection.activeItems.map(item => <CandidateCard
          key={item.recordingId}
          item={item}
          playback={listening.stateFor(item.recordingId)}
          onPreview={() => void listening.toggle(item.recordingId)}
          onPrefetch={() => void listening.prefetch(item.recordingId)}
          onRemove={() => removeCandidate(item.recordingId)}
          removeDisabled={loading || !candidateSelection.canRemove}
        />)}</div>
        {!candidateSelection.canRemove && <p className="candidate-warning" aria-live="polite">候补已用完；你仍可恢复歌曲或重新生成。</p>}
        {candidateSelection.removedItems.length > 0 && <details className="removed"><summary>已移除 {candidateSelection.removedItems.length} 首</summary>{candidateSelection.removedItems.map(item => <div key={item.recordingId}><span><strong>{item.title}</strong><small>{item.artistName}</small></span><button onClick={() => restoreCandidate(item.recordingId)}>恢复</button></div>)}</details>}
        <div className="candidate-actions"><button className="secondary" disabled={loading || Boolean(preparingTournamentId)} onClick={() => void generateCandidates()}>重新生成</button><button className="primary" disabled={loading || candidateSelection.activeItems.length !== size} onClick={startExploration}>{preparingTournamentId ? '重试进入赛事' : '以这组歌曲开赛'}</button></div>{agentFeedback}
      </section>}
    </section>}
    {tournament && <section className="panel arena">{tournament.status === 'COMPLETED' ? <TournamentResultView tournament={tournament} playbackFor={listening.stateFor} onPreview={id => void listening.toggle(id)} onPrefetch={id => void listening.prefetch(id)} onGenerateReport={() => void generateReport()} reportLoading={reportLoading} hasReadyReport={report?.status === 'READY'} agentFeedback={agentFeedback} onNewTournament={() => { listening.stop(); setTournament(null); setReport(null); if(conversationId) window.location.hash='' }}>{report && <ReportView report={report} tournament={tournament} onRetry={() => void generateReport(true)} loading={reportLoading}/>}</TournamentResultView> : <><div className="progress"><span>{`第 ${tournament.completedVoteCount + 1} 场选择`}</span><span>{tournament.completedVoteCount} / {tournament.size - 1}</span></div>{current && left && right && <><h2>这一轮，你更想留下谁？</h2><p className="matchup-hint">点击唱片试听，确定后再选择留下这首。</p><div className="matchup"><SongCard entry={left} playback={listening.stateFor(left.recordingId)} onPreview={() => void listening.toggle(left.recordingId)} onPrefetch={() => void listening.prefetch(left.recordingId)} onVote={() => void vote(left.id)} disabled={loading}/><div className="versus">VS</div><SongCard entry={right} playback={listening.stateFor(right.recordingId)} onPreview={() => void listening.toggle(right.recordingId)} onPrefetch={() => void listening.prefetch(right.recordingId)} onVote={() => void vote(right.id)} disabled={loading}/></div></>}</>}</section>}
  </main>
}

function AgentRunFeedback({ plan, items, collapsed, onToggle }: { plan: AgentPlan | null; items: AgentProgress[]; collapsed: boolean; onToggle: () => void }) {
  return <section className="agent-run-feedback" aria-label="Agent 执行过程">{plan && <AgentPlanPanel plan={plan} collapsed={collapsed} onToggle={onToggle} />}{items.length > 0 && <AgentProgressPanel items={items} collapsed={collapsed} onToggle={onToggle} />}</section>
}

function AgentProgressPanel({ items, collapsed, onToggle }: { items: AgentProgress[]; collapsed: boolean; onToggle: () => void }) {
  const latest = items[items.length - 1]
  return <aside className={`agent-progress ${collapsed ? 'collapsed' : ''}`} aria-live="polite">
    <button className="agent-progress-toggle" onClick={onToggle} aria-expanded={!collapsed}>
      <span>{collapsed ? '已完成本次音乐探索' : '正在梳理这次音乐探索'}</span><small>{latest?.message}</small><span>{collapsed ? '展开' : '收起'}</span>
    </button>
    {!collapsed && <ol>{items.map((item, index) => <li key={`${item.phase}-${index}`}><span>{item.message}</span>{item.elapsedMs != null && <small>{Math.max(1, Math.round(item.elapsedMs / 1000))} 秒</small>}</li>)}</ol>}
  </aside>
}

function AgentPlanPanel({ plan, collapsed, onToggle }: { plan: AgentPlan; collapsed: boolean; onToggle: () => void }) {
  const completed = plan.items.filter(item => item.status === 'completed').length
  return <aside className={`agent-plan ${collapsed ? 'collapsed' : ''}`} aria-live="polite">
    <button className="agent-plan-toggle" onClick={onToggle} aria-expanded={!collapsed}><span>探索计划</span><small>{completed} / {plan.items.length} 已完成</small><span>{collapsed ? '展开' : '收起'}</span></button>
    {!collapsed && <><p>{plan.goal}</p><small className="agent-plan-summary">{plan.summary}</small><ol>{plan.items.map(item => <li key={item.id} className={item.status}><i aria-hidden="true"/>{item.title}<small>{item.detail}</small></li>)}</ol></>}
  </aside>
}

function CandidateCard({ item, playback, onPreview, onPrefetch, onRemove, removeDisabled }: { item: CandidateItem; playback: PlaybackState; onPreview: () => void; onPrefetch: () => void; onRemove: () => void; removeDisabled: boolean }) {
  return <article className="candidate-card">
    <button className="candidate-preview" onClick={onPreview} onMouseEnter={onPrefetch} onFocus={onPrefetch} aria-label={`试听 ${item.artistName}的${item.title}`}>
      {item.coverUrl ? <img src={item.coverUrl} alt={`${item.albumTitle || item.title} 封面`}/> : <div className="cover-placeholder" aria-label="暂无封面">ISQ</div>}
      <strong>{item.title}</strong>
      <small>{item.artistName}{item.albumTitle ? ` · ${item.albumTitle}` : ''}</small>
      {item.catalogSource === 'EXTERNAL_VERIFIED' && <span className="verification-badge">已补充核验歌曲</span>}
      <p>{item.reason}</p>
      {(item.explorationRationale?.length || item.evidenceSummary?.length) ? <details className="candidate-evidence"><summary>探索依据 · {item.verificationStatus === 'VERIFIED' ? 'MusicBrainz 已核验' : '目录已核验'}</summary>{item.explorationRationale?.map((rationale, index) => <p key={`${rationale.kind}-${index}`}>{rationale.text}</p>)}{item.evidenceSummary?.map((evidence, index) => <a key={`${evidence.url}-${index}`} href={evidence.url} target="_blank" rel="noreferrer">参考：{evidence.title || evidence.domain || '公开音乐资料'}</a>)}</details> : null}
      <span className={`playback-state ${playback.phase}`}>{playbackLabel(playback.phase)}</span>
    </button>
    <ListeningLinks playback={playback} searchUrl={item.listeningSearchUrl}/>
    <button className="remove-candidate" onClick={onRemove} disabled={removeDisabled}>移除</button>
  </article>
}

function ArtistClarificationView({ clarifications, loading, feedback, onConfirm, onRestart }: { clarifications: NonNullable<CandidatePoolResponse['clarifications']>; loading: boolean; feedback?: ReactNode; onConfirm: (choices: { mention: string; mbid: string; name: string }[]) => void; onRestart: () => void }) {
  const [selected, setSelected] = useState<Record<string, ArtistChoice>>({})
  const complete = clarifications.length > 0 && clarifications.every(item => selected[item.mention])
  return <section className="candidate-state clarification-state" aria-live="polite">
    <p className="eyebrow">需要确认艺人</p><h2>先确认这些名字，再开始找歌</h2>
    <p className="subtitle">系统检测到可能对应多个音乐人的名称。为避免把错误的作品放进候选池，请选择你指的对象。</p>
    {clarifications.map(item => <fieldset key={item.mention} className="artist-clarification"><legend>{item.mention}</legend>{item.candidates.length ? item.candidates.map(candidate => <label key={candidate.mbid} className={selected[item.mention]?.mbid === candidate.mbid ? 'selected' : ''}><input type="radio" name={`artist-${item.mention}`} checked={selected[item.mention]?.mbid === candidate.mbid} onChange={() => setSelected(current => ({ ...current, [item.mention]: candidate }))}/><span><strong>{candidate.name}</strong><small>{[candidate.type, candidate.country, candidate.disambiguation, candidate.begin && `始于 ${candidate.begin}`].filter(Boolean).join(' · ') || 'MusicBrainz 艺人条目'}</small></span></label>) : <p className="candidate-warning">没有找到可安全确认的条目，请修正该艺人的拼写或补充更多信息。</p>}</fieldset>)}
    <div className="candidate-actions"><button className="secondary" onClick={onRestart}>以上都不是，修改输入</button><button className="primary" disabled={loading || !complete} onClick={() => onConfirm(clarifications.map(item => ({ mention: item.mention, mbid: selected[item.mention].mbid, name: selected[item.mention].name })))}>{loading ? '正在继续寻找…' : '确认并继续生成'}</button></div>{feedback}
  </section>
}

function SongCard({ entry, playback, onPreview, onPrefetch, onVote, disabled }: { entry: Entry; playback: PlaybackState; onPreview: () => void; onPrefetch: () => void; onVote: () => void; disabled: boolean }) {
  return <article className="song-card">
    <button className="song-preview" onClick={onPreview} onMouseEnter={onPrefetch} onFocus={onPrefetch} aria-label={`试听 ${entry.artistName}的${entry.title}`}>
      {entry.coverUrl ? <img src={entry.coverUrl} alt={`${entry.albumTitle ?? entry.title} 封面`}/> : <div className="cover-placeholder" aria-label="暂无封面">ISQ</div>}
      <span>{entry.title}</span><small>{entry.artistName}</small>
      <span className={`playback-state ${playback.phase}`}>{playbackLabel(playback.phase)}</span>
    </button>
    <ListeningLinks playback={playback} searchUrl={entry.listeningSearchUrl}/>
    <button className="vote-song" onClick={onVote} disabled={disabled}>留下这首</button>
  </article>
}

function ListeningLinks({ playback, searchUrl }: { playback: PlaybackState; searchUrl: string }) {
  return <div className="listening-links">
    {playback.options?.preview && <a href={playback.options.preview.providerTrackUrl} target="_blank" rel="noreferrer">{playback.options.preview.attribution} · 查看条目</a>}
    <a href={searchUrl} target="_blank" rel="noreferrer">去网易云搜索</a>
  </div>
}

function ReportView({ report, tournament, onRetry, loading }: { report: Report; tournament: Tournament; onRetry: () => void; loading: boolean }) {
  const exportRef = useRef<HTMLElement>(null)
  const [exporting, setExporting] = useState(false)
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const finalMatch = [...tournament.matches].sort((a, b) => b.roundNumber - a.roundNumber || b.matchIndex - a.matchIndex)[0]
  const champion = finalMatch?.winnerEntryId ? tournament.entries.find(entry => entry.id === finalMatch.winnerEntryId) : undefined
  async function createPreview() {
    if (!exportRef.current) return
    setExporting(true)
    try { setPreviewUrl(await toPng(exportRef.current, { backgroundColor: '#fdf9f4', pixelRatio: 2, cacheBust: true })) }
    catch { alert('长图生成失败，请稍后重试。') }
    finally { setExporting(false) }
  }
  function download() { if (!previewUrl) return; const link = document.createElement('a'); link.href = previewUrl; link.download = `indie-sound-quest-report-${report.version}.png`; link.click() }
  if (report.status === 'PENDING' || report.status === 'RUNNING') return <div className="report-state"><span className="eyebrow">偏好报告</span><p>正在整理你的选择轨迹、偏好信号和探索方向…</p></div>
  if (report.status === 'FAILED') return <div className="report-state"><span className="eyebrow">生成未完成</span><p>{report.failureMessage || '这次分析没有完成，可以稍后重试。'}</p><button className="secondary" disabled={loading} onClick={onRetry}>重试</button></div>
  if (!report.report) return null
  return <><article className="preference-report"><div className="report-heading"><span className="eyebrow">本场偏好报告 · 第 {report.version} 版</span><h2>你在这场比赛里留下的声音</h2></div><p className="report-summary">{report.report.summary}</p>{report.report.explorationTags?.length ? <section className="report-exploration"><span className="eyebrow">探索依据</span><div>{report.report.explorationTags.map(tag => <span key={tag}>{tag}</span>)}</div></section> : null}<div className="dimension-grid">{report.report.dimensions.map(dimension => <section className="dimension" key={dimension.name}><div><strong>{dimension.name}</strong><small>{dimension.confidence} 置信</small></div><p>{dimension.explanation}</p></section>)}</div>{report.report.choiceTrajectory?.length ? <section className="choice-trajectory"><span className="eyebrow">你的选择轨迹</span>{report.report.choiceTrajectory.map(item => <article key={item.matchId}><small>第 {item.roundNumber} 轮</small><p><strong>《{item.winnerTitle}》</strong><span>{item.winnerArtistName}</span><i>胜出于</i><strong>《{item.loserTitle}》</strong><span>{item.loserArtistName}</span></p><em>{item.derivedNote}</em></article>)}</section> : null}<h3>可以继续听听</h3><div className="report-links"><div><span className="eyebrow">歌曲方向</span>{report.report.songRecommendations.map((item, index) => <RecommendationCard key={item.recordingId || item.sourceUrl || index} title={item.title || '继续探索这首歌'} subtitle={item.artistName} item={item}/>)}</div><div><span className="eyebrow">艺人方向</span>{report.report.artistRecommendations.map((item, index) => <RecommendationCard key={item.artistId || item.sourceUrl || index} title={item.artistName || '继续探索这位艺人'} item={item}/>)}</div></div><blockquote>{report.report.personalityEasterEgg}</blockquote><p className="disclaimer">{report.report.disclaimer}</p><button className="secondary export-trigger" disabled={exporting} onClick={() => void createPreview()}>{exporting ? '正在生成长图…' : '预览报告长图'}</button></article><ReportExport report={report} champion={champion} tournament={tournament} exportRef={exportRef}/>{previewUrl && <div className="export-modal" role="dialog" aria-modal="true" aria-label="报告长图预览"><div><button className="modal-close" onClick={() => setPreviewUrl(null)}>关闭</button><img src={previewUrl} alt="偏好报告长图预览"/><button className="primary" onClick={download}>下载 PNG 长图</button></div></div>}</>
}

function ReportExport({ report, champion, tournament, exportRef }: { report: Report; champion?: Entry; tournament: Tournament; exportRef: RefObject<HTMLElement> }) {
  if (!report.report) return null
  return <article className="report-export" ref={exportRef} aria-hidden="true"><p className="eyebrow">本场冠军 · {tournament.size} 首歌曲世界杯</p><section className="export-champion">{champion?.coverUrl ? <img src={champion.coverUrl} alt=""/> : <div className="export-disc">ISQ</div>}<div><h1>{champion?.title || '本场冠军'}</h1><p>{champion?.artistName || 'IndieSoundQuest'}</p></div></section><p className="eyebrow">偏好报告 · 第 {report.version} 版</p><p className="export-summary">{report.report.summary}</p>{report.report.explorationTags?.length ? <p className="export-tags">探索依据 · {report.report.explorationTags.join(' · ')}</p> : null}<div className="export-dimensions">{report.report.dimensions.map(item => <section key={item.name}><strong>{item.name}</strong><p>{item.explanation}</p></section>)}</div>{report.report.choiceTrajectory?.length ? <><h2>你的选择轨迹</h2>{report.report.choiceTrajectory.map(item => <p className="export-item" key={item.matchId}><strong>《{item.winnerTitle}》</strong> · {item.winnerArtistName}<br/><small>胜出于《{item.loserTitle}》· {item.loserArtistName}｜{item.derivedNote}</small></p>)}</> : null}<h2>继续听听</h2>{report.report.songRecommendations.map((item, index) => <p className="export-item" key={item.recordingId || index}><strong>{item.title}</strong> {item.artistName && `· ${item.artistName}`}<br/><small>{item.reason}{item.sourceStatus === 'web_discovered' ? `｜网络发现 · 待核验｜${item.sourceTitle || '来源资料'}｜请以音乐平台搜索结果为准` : ''}</small></p>)}<blockquote>{report.report.personalityEasterEgg}</blockquote><p className="export-disclaimer">{report.report.disclaimer}</p><footer>IndieSoundQuest</footer></article>
}

function RecommendationCard({ title, subtitle, item }: { title: string; subtitle?: string; item: { reason: string; searchUrl?: string; sourceStatus?: string; sourceUrl?: string; sourceTitle?: string } }) {
  const webDiscovered = item.sourceStatus === 'web_discovered'
  return <article className="recommendation-card"><a href={item.searchUrl || '#'} target="_blank" rel="noreferrer"><strong>{title}</strong>{webDiscovered && <span className="source-badge">网络发现 · 待核验</span>}<small>{subtitle ? `${subtitle} · ` : ''}{item.reason}</small></a>{webDiscovered && item.sourceUrl && <a className="source-link" href={item.sourceUrl} target="_blank" rel="noreferrer">查看发现来源{item.sourceTitle ? `：${item.sourceTitle}` : ''}</a>}</article>
}
