import { FormEvent, useEffect, useMemo, useRef, useState } from 'react'
import type { CandidateItem, CandidatePoolResponse } from './candidatePool'
import './conversation.css'
import './tournamentLaunch.css'

type Conversation = { id: string; title: string; summary?: string | null; status: 'ACTIVE' | 'ARCHIVED' | 'DELETED'; lastMessageAt: string }
type Message = { id: string; role: 'USER' | 'ASSISTANT' | 'SYSTEM'; type: 'USER_TEXT' | 'AGENT_TEXT' | 'AGENT_RUN' | 'SYSTEM_NOTE' | 'TOURNAMENT_CARD' | 'CANDIDATE_POOL_CARD' | 'REPORT_CARD' | 'CLARIFICATION_CARD'; content?: string | null; cardType?: string | null; cardPayloadJson?: string | null; status: 'RUNNING' | 'COMPLETED' | 'FAILED'; sequenceNumber: number; createdAt: string }
type Progress = { phase: string; message: string; elapsedMs?: number }
type Plan = { revision: number; goal: string; summary: string; items: { id: string; title: string; status: 'pending' | 'running' | 'completed'; detail: string }[] }
type CardItem = Pick<CandidateItem, 'recordingId' | 'title' | 'artistName' | 'coverUrl'>
type CardPayload = { size?: 16 | 32; status?: string; summary?: string; preferenceText?: string; items?: CardItem[] }
type Tournament = { id: string; status: string; size: number; completedVoteCount: number; currentMatch: { id: string; leftEntryId: string; rightEntryId: string } | null; entries: { id: string; title: string; artistName: string; coverUrl?: string }[] }
type Report = { status: string; reportId?: string; version?: number; report?: { summary?: string; preferenceDimensions?: { label?: string; summary?: string }[]; songRecommendations?: { title?: string; artistName?: string; reason?: string; searchUrl?: string }[]; artistRecommendations?: { artistName?: string; reason?: string; searchUrl?: string }[]; personalityEasterEgg?: string } }

async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api/v1${path}`, { credentials: 'include', ...options })
  if (!response.ok) throw new Error(`请求失败（${response.status}）`)
  return response.json() as Promise<T>
}

function friendlyError(error: unknown) {
  const text = error instanceof Error ? error.message : ''
  if (/load failed|failed to fetch|networkerror/i.test(text)) return '连接意外中断了，请检查网络后重试。'
  return text || '这次音乐对话暂时无法完成，请稍后重试。'
}

async function stream<T>(path: string, options: RequestInit, onProgress: (item: Progress) => void, onPlan: (item: Plan) => void): Promise<T> {
  const response = await fetch(`/api/v1${path}`, { credentials: 'include', ...options })
  if (!response.ok || !response.body) throw new Error(`请求失败（${response.status}）`)
  const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = ''; let result: T | undefined
  const consume = (block: string) => {
    const event = block.match(/^event:\s*(.+)$/m)?.[1]?.trim(); const data = block.match(/^data:\s*(.+)$/m)?.[1]?.trim()
    if (!event || !data) return
    if (event === 'progress') onProgress(JSON.parse(data) as Progress)
    else if (event === 'plan_updated') onPlan(JSON.parse(data) as Plan)
    else if (event === 'result') result = JSON.parse(data) as T
    else if (event === 'error') throw new Error((JSON.parse(data) as { message?: string }).message || 'Agent 服务暂时不可用')
  }
  while (true) {
    const { value, done } = await reader.read(); buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done })
    let boundary: number
    while ((boundary = buffer.indexOf('\n\n')) >= 0) { consume(buffer.slice(0, boundary)); buffer = buffer.slice(boundary + 2) }
    if (done) break
  }
  if (!result) throw new Error('Agent 未返回最终结果')
  return result
}

export function ConversationApp() {
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [current, setCurrent] = useState<Conversation | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [draft, setDraft] = useState('')
  const [pending, setPending] = useState(false)
  const [progress, setProgress] = useState<Progress[]>([])
  const [plan, setPlan] = useState<Plan | null>(null)
  const [collapsed, setCollapsed] = useState(false)
  const [notice, setNotice] = useState('')
  const [candidateRun, setCandidateRun] = useState<{ size: 16 | 32; preferenceText: string } | null>(null)
  const timelineRef = useRef<HTMLElement>(null)

  useEffect(() => { void boot() }, [])
  useEffect(() => { timelineRef.current?.scrollTo({ top: timelineRef.current.scrollHeight, behavior: 'smooth' }) }, [messages, pending, candidateRun])

  async function refreshMessages(conversationId = current?.id) {
    if (!conversationId) return
    setMessages(await api<Message[]>(`/conversations/${conversationId}/messages`))
  }
  async function boot() {
    try { const list = await api<Conversation[]>('/conversations'); setConversations(list); if (list[0]) await open(list[0]); else await createConversation() } catch (error) { setNotice(friendlyError(error)) }
  }
  async function createConversation() {
    try { const created = await api<Conversation>('/conversations', { method: 'POST' }); setConversations(previous => [created, ...previous]); setCurrent(created); setMessages([]); setNotice('') } catch (error) { setNotice(friendlyError(error)) }
  }
  async function open(conversation: Conversation) {
    if (pending || candidateRun) return
    try { await refreshMessages(conversation.id); setCurrent(conversation); setProgress([]); setPlan(null); setNotice('') } catch (error) { setNotice(friendlyError(error)) }
  }
  async function persistCard(type: 'CANDIDATE_POOL_CARD' | 'TOURNAMENT_CARD' | 'REPORT_CARD', cardType: string, payload: object) {
    if (!current) return
    await api(`/conversations/${current.id}/cards`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID() }, body: JSON.stringify({ type, cardType, payloadJson: JSON.stringify(payload) }) })
    await refreshMessages()
  }
  async function send(event: FormEvent) {
    event.preventDefault(); const content = draft.trim(); if (!content || !current || pending || candidateRun) return
    const clientId = crypto.randomUUID(); const optimistic: Message = { id: clientId, role: 'USER', type: 'USER_TEXT', content, status: 'COMPLETED', sequenceNumber: Date.now(), createdAt: new Date().toISOString() }
    setMessages(previous => [...previous, optimistic]); setDraft(''); setPending(true); setProgress([]); setPlan(null); setCollapsed(false); setNotice('')
    try {
      const response = await fetch(`/api/v1/conversations/${current.id}/messages:stream`, { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': clientId }, body: JSON.stringify({ content }) })
      if (!response.ok || !response.body) throw new Error(`请求失败（${response.status}）`)
      const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = ''; let completed = false
      const consume = (block: string) => { const type = block.match(/^event:\s*(.+)$/m)?.[1]?.trim(); const data = block.match(/^data:\s*(.+)$/m)?.[1]?.trim(); if (!type || !data) return; if (type === 'progress') setProgress(previous => [...previous, JSON.parse(data) as Progress]); else if (type === 'plan_updated') setPlan(JSON.parse(data) as Plan); else if (type === 'message_completed') completed = true; else if (type === 'error') throw new Error((JSON.parse(data) as { message?: string }).message || '这次音乐对话暂时无法完成') }
      while (true) { const { value, done } = await reader.read(); buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done }); let boundary: number; while ((boundary = buffer.indexOf('\n\n')) >= 0) { consume(buffer.slice(0, boundary)); buffer = buffer.slice(boundary + 2) } if (done) break }
      if (!completed) throw new Error('Agent 未返回最终回复')
      await refreshMessages(); const refreshed = await api<Conversation[]>('/conversations'); setConversations(refreshed); setCurrent(refreshed.find(item => item.id === current.id) ?? current)
    } catch (error) { setMessages(previous => previous.filter(item => item.id !== clientId)); setDraft(content); setNotice(friendlyError(error)) } finally { setPending(false); setCollapsed(true) }
  }
  function startCandidateRun(size: 16 | 32, preferenceText: string) {
    if (!preferenceText.trim()) { setNotice('先描述一下你想放进世界杯的音乐偏好。'); return }
    setCandidateRun({ size, preferenceText }); setProgress([]); setPlan(null); setCollapsed(false); setNotice('')
  }
  const displayMessages = useMemo(() => messages.filter(message => message.type !== 'AGENT_RUN'), [messages])
  return <main className="conversation-shell">
    <aside className="conversation-sidebar"><button className="conversation-brand" type="button">INDIESOUNDQUEST <small>歌曲世界杯</small></button><button className="new-conversation" onClick={() => void createConversation()} disabled={pending || Boolean(candidateRun)}>新建探索</button><p className="conversation-label">最近探索</p><nav>{conversations.map(conversation => <button key={conversation.id} onClick={() => void open(conversation)} className={conversation.id === current?.id ? 'active' : ''}><strong>{conversation.title}</strong><small>{new Date(conversation.lastMessageAt).toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' })}</small></button>)}</nav></aside>
    <section className="conversation-main"><header className="conversation-header"><p className="eyebrow">音乐探索对话</p><h1>{current?.title || '新的音乐探索'}</h1><p>从一句偏好开始，候选、对局和报告都会留在这段对话里。</p></header>{notice && <p className="conversation-notice">{notice}</p>}
      <section className="conversation-timeline" ref={timelineRef} aria-live="polite">
        {displayMessages.length === 0 && <Welcome onSelect={(text) => setDraft(text)} />}
        {displayMessages.map((message, index) => {
          if (message.type === 'TOURNAMENT_CARD' && message.cardType === 'WORLD_CUP_LAUNCH') return <TournamentLaunchCard key={message.id} payload={message.cardPayloadJson} onStart={startCandidateRun} />
          if (message.type === 'CANDIDATE_POOL_CARD') return <CandidatePoolCard key={message.id} cardMessageId={message.id} alreadyStarted={displayMessages.some(candidateTournamentCardFor(message.id)) || legacyCandidateHasFollowingTournament(displayMessages, index)} payload={message.cardPayloadJson} onRebuild={startCandidateRun} onTournamentCreated={(tournamentId, size, sourceCandidateMessageId) => void persistCard('TOURNAMENT_CARD', 'TOURNAMENT', { tournamentId, size, status: 'READY', sourceCandidateMessageId })} />
          if (message.type === 'TOURNAMENT_CARD') return <TournamentStatusCard key={message.id} payload={message.cardPayloadJson} onReportReady={(report, tournamentId, championTitle) => void persistCard('REPORT_CARD', 'PREFERENCE_REPORT', { tournamentId, reportId: report.reportId, version: report.version, status: report.status, championTitle })} />
          if (message.type === 'REPORT_CARD') return <ReportCard key={message.id} payload={message.cardPayloadJson} />
          return <article className={`conversation-message ${message.role === 'USER' ? 'user' : 'agent'}`} key={message.id}><p>{message.content}</p></article>
        })}
        {candidateRun && <CandidateGenerationCard size={candidateRun.size} preferenceText={candidateRun.preferenceText} onProgress={(item) => setProgress(previous => [...previous, item])} onPlan={setPlan} onCompleted={async payload => { await persistCard('CANDIDATE_POOL_CARD', 'CANDIDATE_POOL', payload); setCandidateRun(null); setCollapsed(true) }} onFailed={message => { setNotice(message); setCandidateRun(null); setCollapsed(true) }} />}
      </section>
      <form className="conversation-composer" onSubmit={event => void send(event)}><textarea value={draft} onChange={event => setDraft(event.target.value)} placeholder="说说你想探索的音乐…" maxLength={2000} disabled={!current || pending || Boolean(candidateRun)} /><button type="submit" disabled={!draft.trim() || !current || pending || Boolean(candidateRun)}>{pending ? '正在整理…' : '发送'}</button></form>
    </section>
    <aside className="conversation-agent-panel">{(pending || candidateRun || plan || progress.length > 0) ? <AgentRun plan={plan} progress={progress} collapsed={collapsed} active={pending || Boolean(candidateRun)} onToggle={() => setCollapsed(value => !value)} /> : <div className="conversation-agent-empty"><p className="eyebrow">Agent 工作区</p><strong>等待下一次音乐探索</strong><small>开始对话后，这里会显示滚动计划、工具进度与任务状态。</small></div>}</aside>
  </main>
}

function Welcome({ onSelect }: { onSelect: (text: string) => void }) { return <div className="conversation-welcome"><p className="eyebrow">歌曲世界杯</p><h2>我们的歌曲世界杯应该从哪开始？</h2><p>描述一下你的音乐喜好，或告诉我最近反复听的歌。我会先和你一起澄清方向、构建候选池，再把它们放进一场比赛。</p><div><button onClick={() => onSelect('我喜欢徐佳莹、艾怡良和郑宜农，想做一场适合深夜的歌曲世界杯。')}>从华语创作女声开始</button><button onClick={() => onSelect('想做一场适合夜晚散步的独立流行歌曲世界杯。')}>寻找夜晚散步的歌</button></div></div> }
export function candidateTournamentCardFor(candidateMessageId: string) { return (message: Message) => { if (message.type !== 'TOURNAMENT_CARD') return false; try { return JSON.parse(message.cardPayloadJson || '{}').sourceCandidateMessageId === candidateMessageId } catch { return false } } }
export function legacyCandidateHasFollowingTournament(messages: Message[], candidateIndex: number) { const following = messages.slice(candidateIndex + 1); const nextCandidateIndex = following.findIndex(message => message.type === 'CANDIDATE_POOL_CARD'); return following.slice(0, nextCandidateIndex < 0 ? following.length : nextCandidateIndex).some(message => message.type === 'TOURNAMENT_CARD') }
function AgentRun({ plan, progress, collapsed, active, onToggle }: { plan: Plan | null; progress: Progress[]; collapsed: boolean; active: boolean; onToggle: () => void }) { const latest = progress[progress.length - 1]; return <aside className={`conversation-run ${collapsed ? 'collapsed' : ''}`}><button onClick={onToggle}><span>{active ? '正在整理这次音乐探索' : '已完成本次音乐探索'}</span><small>{latest?.message || plan?.summary}</small><span>{collapsed ? '展开' : '收起'}</span></button>{!collapsed && <>{plan && <ol className="conversation-plan">{plan.items.map(item => <li key={item.id} className={item.status}><span>{item.title}</span><small>{item.detail}</small></li>)}</ol>}{progress.length > 0 && <ol className="conversation-progress">{progress.map((item, index) => <li key={`${item.phase}-${index}`}>{item.message}</li>)}</ol>}</>}</aside> }
function TournamentLaunchCard({ payload, onStart }: { payload?: string | null; onStart: (size: 16 | 32, preferenceText: string) => void }) { let title = '把这轮偏好放进一场比赛'; let preferenceText = ''; try { const value = JSON.parse(payload || '{}'); title = value.title || title; preferenceText = value.preferenceText || '' } catch { /* malformed legacy card */ } return <section className="tournament-launch-card"><p className="eyebrow">歌曲世界杯</p><h2>{title}</h2><p>先由 Agent 检索并核验候选歌曲；你确认后才会开始两两对决。</p><div><button onClick={() => onStart(16, preferenceText)}>构建 16 首候选池</button><button onClick={() => onStart(32, preferenceText)}>构建 32 首候选池</button></div></section> }
function CandidateGenerationCard({ size, preferenceText, onProgress, onPlan, onCompleted, onFailed }: { size: 16 | 32; preferenceText: string; onProgress: (item: Progress) => void; onPlan: (item: Plan) => void; onCompleted: (payload: CardPayload) => Promise<void>; onFailed: (message: string) => void }) { const [status, setStatus] = useState('正在理解你的偏好并寻找歌曲…'); useEffect(() => { let alive = true; void stream<CandidatePoolResponse>('/agent-runs/candidate-pool:stream', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Request-Id': crypto.randomUUID() }, body: JSON.stringify({ size, preferenceText, seedArtistIds: [], confirmedArtists: [] }) }, item => { if (alive) { setStatus(item.message); onProgress(item) } }, onPlan).then(async result => { if (!alive) return; if (result.status !== 'ready_for_confirmation' || !result.candidatePool) { onFailed(result.candidatePool?.warnings?.[0]?.message || '候选池暂时不足以开赛，请补充偏好后重试。'); return } await onCompleted({ size, status: result.status, preferenceText, summary: result.candidatePool.candidateSummary, items: result.candidatePool.items.map(item => ({ recordingId: item.recordingId, title: item.title, artistName: item.artistName, coverUrl: item.coverUrl })) }) }).catch(error => alive && onFailed(friendlyError(error))); return () => { alive = false } }, [size, preferenceText]); return <section className="tournament-launch-card"><p className="eyebrow">候选池生成中</p><h2>正在为这场 {size} 首比赛挑选歌曲</h2><p>{status}</p></section> }
function CandidatePoolCard({ cardMessageId, alreadyStarted, payload, onRebuild, onTournamentCreated }: { cardMessageId: string; alreadyStarted: boolean; payload?: string | null; onRebuild: (size: 16 | 32, preferenceText: string) => void; onTournamentCreated: (id: string, size: number, sourceCandidateMessageId: string) => void }) { let value: CardPayload = {}; try { value = JSON.parse(payload || '{}') } catch { /* display degraded card */ } const size = value.size || 16; const [removed, setRemoved] = useState<string[]>([]); const [starting, setStarting] = useState(false); const [created, setCreated] = useState<string | null>(null); const [error, setError] = useState(''); const active = (value.items || []).filter(item => !removed.includes(item.recordingId)).slice(0, size); const remove = (id: string) => setRemoved(current => current.includes(id) ? current : [...current, id]); const start = async () => { if (active.length !== size) return; setStarting(true); setError(''); try { const tournament = await api<{ id: string; status: string }>('/tournaments', { method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID() }, body: JSON.stringify({ size, candidateSource: 'AGENT_GENERATED', recordingIds: active.map(item => item.recordingId), explorationBrief: value.preferenceText || value.summary || '' }) }); if (tournament.status === 'DRAFT') await api(`/tournaments/${tournament.id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status: 'READY' }) }); setCreated(tournament.id); onTournamentCreated(tournament.id, size, cardMessageId) } catch { setError('赛事创建失败，请重试。') } finally { setStarting(false) } }; if (created || alreadyStarted) return <section className="tournament-launch-card"><p className="eyebrow">候选歌曲池</p><h2>赛事已创建</h2><p>首场对局已写入下方的赛事卡；你的候选池仍保留在这条对话中。</p></section>; const hasSnapshot = active.length > 0; return <section className="tournament-launch-card"><p className="eyebrow">候选歌曲池</p><h2>{value.status === 'ready_for_confirmation' ? `已为 ${size} 首赛事整理候选` : '候选池需要继续调整'}</h2><p>{value.summary || '候选池已保存；可在当前会话内继续确认与开赛。'}</p>{hasSnapshot ? <ol className="conversation-candidate-preview">{active.slice(0, 10).map(item => <li key={item.recordingId}>{item.coverUrl && <img src={item.coverUrl} alt="" />}<span><strong>{item.title}</strong><small>{item.artistName}</small></span><button onClick={() => remove(item.recordingId)} disabled={starting}>移除</button></li>)}{active.length > 10 && <li className="more">以及另外 {active.length - 10} 首参赛候选</li>}</ol> : <p className="conversation-card-count">这是较早版本保存的候选卡，缺少可展示的歌曲快照。重新构建后会把完整候选留在当前对话中。</p>}{hasSnapshot && <p className="conversation-card-count">当前参赛 {active.length} / {size} 首；移除后会由候补自动补位。</p>}{error && <p className="conversation-card-error">{error}</p>}<div>{hasSnapshot ? <button disabled={starting || active.length !== size} onClick={() => void start()}>{starting ? '正在创建赛事…' : '确认这组歌曲并开赛'}</button> : <button onClick={() => onRebuild(size, value.preferenceText || value.summary || '根据本轮已有的音乐偏好重新构建候选歌曲池')}>重新构建完整候选池</button>}</div></section> }
function TournamentStatusCard({ payload, onReportReady }: { payload?: string | null; onReportReady: (report: Report, tournamentId: string, championTitle: string) => void }) { let value: { tournamentId?: string; size?: number } = {}; try { value = JSON.parse(payload || '{}') } catch { /* display degraded card */ } const [tournament, setTournament] = useState<Tournament | null>(null); const [loading, setLoading] = useState(false); const [reporting, setReporting] = useState(false); const [error, setError] = useState(''); const refresh = async () => { if (!value.tournamentId) return; try { setTournament(await api<Tournament>(`/tournaments/${value.tournamentId}`)) } catch { setError('赛事状态暂时无法读取。') } }; useEffect(() => { void refresh() }, [value.tournamentId]); const current = tournament?.currentMatch; const entries = new Map(tournament?.entries.map(item => [item.id, item]) || []); const left = current ? entries.get(current.leftEntryId) : undefined; const right = current ? entries.get(current.rightEntryId) : undefined; const vote = async (entryId: string) => { if (!current) return; setLoading(true); setError(''); try { await api(`/tournament-matches/${current.id}/votes`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID() }, body: JSON.stringify({ selectedEntryId: entryId }) }); await refresh() } catch { setError('这次选择没有保存成功，请重试。') } finally { setLoading(false) } }; const generateReport = async () => { if (!value.tournamentId || !tournament) return; setReporting(true); setError(''); try { const report = await stream<Report>(`/tournaments/${value.tournamentId}/preference-report:stream`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ force: false }) }, () => undefined, () => undefined); const champion = tournament.entries[0]?.title || ''; onReportReady(report, value.tournamentId, champion) } catch (error) { setError(friendlyError(error)) } finally { setReporting(false) } }; return <section className="tournament-launch-card conversation-match-card"><p className="eyebrow">歌曲世界杯 · {tournament?.status === 'COMPLETED' ? '已结束' : '进行中'}</p><h2>{tournament?.status === 'COMPLETED' ? '这场比赛已结束' : `${tournament?.size || value.size || 16} 首歌曲世界杯`}</h2>{tournament && <p>已完成 {tournament.completedVoteCount} / {tournament.size - 1} 场选择。</p>}{current && left && right ? <div className="conversation-match-choice"><button disabled={loading} onClick={() => void vote(left.id)}>{left.coverUrl && <img src={left.coverUrl} alt="" />}<strong>{left.title}</strong><small>{left.artistName}</small></button><span>VS</span><button disabled={loading} onClick={() => void vote(right.id)}>{right.coverUrl && <img src={right.coverUrl} alt="" />}<strong>{right.title}</strong><small>{right.artistName}</small></button></div> : tournament?.status === 'COMPLETED' ? <p>冠军已经产生。现在可以把本场选择沉淀为偏好报告。</p> : <p>正在准备下一场对局…</p>}{error && <p className="conversation-card-error">{error}</p>}<div><button onClick={() => void refresh()}>{loading ? '正在保存…' : '刷新赛事状态'}</button>{tournament?.status === 'COMPLETED' && <button onClick={() => void generateReport()} disabled={reporting}>{reporting ? '正在生成报告…' : '生成赛后报告'}</button>}</div></section> }
function ReportCard({ payload }: { payload?: string | null }) { let value: { tournamentId?: string; status?: string; championTitle?: string } = {}; try { value = JSON.parse(payload || '{}') } catch { /* display degraded card */ } const [report, setReport] = useState<Report | null>(null); const [expanded, setExpanded] = useState(false); useEffect(() => { if (value.tournamentId) void api<Report>(`/tournaments/${value.tournamentId}/preference-report`).then(setReport).catch(() => undefined) }, [value.tournamentId]); const ready = report?.status === 'READY' || value.status === 'READY'; const body = report?.report; return <section className="tournament-launch-card conversation-report-card"><p className="eyebrow">赛后偏好报告</p><h2>{value.championTitle ? `冠军《${value.championTitle}》的探索报告` : '本场音乐探索报告'}</h2><p>{ready ? (body?.summary || '报告已完成。你可以继续追问本场的选择轨迹与推荐方向。') : '报告正在生成中。'}</p>{ready && body?.songRecommendations?.length ? <ol className="conversation-recommendations">{body.songRecommendations.slice(0, expanded ? 7 : 3).map((item, index) => <li key={`${item.title}-${index}`}><strong>{item.title}</strong><small>{item.artistName} · {item.reason}</small>{item.searchUrl && <a href={item.searchUrl} target="_blank" rel="noreferrer">去平台搜索</a>}</li>)}</ol> : null}{expanded && body?.artistRecommendations?.length ? <ol className="conversation-recommendations">{body.artistRecommendations.map((item, index) => <li key={`${item.artistName}-${index}`}><strong>{item.artistName}</strong><small>{item.reason}</small>{item.searchUrl && <a href={item.searchUrl} target="_blank" rel="noreferrer">去平台搜索</a>}</li>)}</ol> : null}{expanded && body?.personalityEasterEgg && <p className="conversation-report-easter-egg">{body.personalityEasterEgg}</p>}<div>{ready && <button onClick={() => setExpanded(value => !value)}>{expanded ? '收起报告详情' : '展开完整报告'}</button>}</div></section> }
