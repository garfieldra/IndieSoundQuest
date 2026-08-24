import { FormEvent, useEffect, useMemo, useRef, useState } from 'react'
import './conversation.css'
import './tournamentLaunch.css'

type Conversation = { id: string; title: string; summary?: string | null; status: 'ACTIVE' | 'ARCHIVED' | 'DELETED'; lastMessageAt: string }
type Message = { id: string; agentRunId?: string | null; role: 'USER' | 'ASSISTANT' | 'SYSTEM'; type: 'USER_TEXT' | 'AGENT_TEXT' | 'AGENT_RUN' | 'SYSTEM_NOTE' | 'TOURNAMENT_CARD' | 'CANDIDATE_POOL_CARD' | 'REPORT_CARD' | 'CLARIFICATION_CARD'; content?: string | null; cardType?: string | null; cardPayloadJson?: string | null; status: 'RUNNING' | 'COMPLETED' | 'FAILED'; sequenceNumber: number; createdAt: string }
type Progress = { phase: string; message: string; elapsedMs?: number }
type Plan = { revision: number; goal: string; summary: string; items: { id: string; title: string; status: 'pending' | 'running' | 'completed'; detail: string }[] }

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
  const [tournamentPrompt, setTournamentPrompt] = useState('')
  const timelineRef = useRef<HTMLElement>(null)

  useEffect(() => { void boot() }, [])
  useEffect(() => { timelineRef.current?.scrollTo({ top: timelineRef.current.scrollHeight, behavior: 'smooth' }) }, [messages, pending])

  async function boot() {
    try {
      const list = await api<Conversation[]>('/conversations')
      setConversations(list)
      if (list[0]) await open(list[0])
      else await createConversation()
    } catch (error) { setNotice(friendlyError(error)) }
  }

  async function createConversation() {
    try {
      const created = await api<Conversation>('/conversations', { method: 'POST' })
      setConversations(previous => [created, ...previous])
      setCurrent(created); setMessages([]); setNotice('')
    } catch (error) { setNotice(friendlyError(error)) }
  }

  async function open(conversation: Conversation) {
    if (pending) return
    try {
      const history = await api<Message[]>(`/conversations/${conversation.id}/messages`)
      setCurrent(conversation); setMessages(history); setProgress([]); setPlan(null); setTournamentPrompt(''); setNotice('')
    } catch (error) { setNotice(friendlyError(error)) }
  }

  async function send(event: FormEvent) {
    event.preventDefault()
    const content = draft.trim()
    if (!content || !current || pending) return
    const clientId = crypto.randomUUID()
    const optimistic: Message = { id: clientId, role: 'USER', type: 'USER_TEXT', content, status: 'COMPLETED', sequenceNumber: Date.now(), createdAt: new Date().toISOString() }
    setMessages(previous => [...previous, optimistic]); setDraft(''); setPending(true); setProgress([]); setPlan(null); setCollapsed(false); setNotice('')
    try {
      const response = await fetch(`/api/v1/conversations/${current.id}/messages:stream`, {
        method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': clientId }, body: JSON.stringify({ content }),
      })
      if (!response.ok || !response.body) throw new Error(`请求失败（${response.status}）`)
      const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = ''; let finalMessage: Message | null = null
      const consume = (block: string) => {
        const type = block.match(/^event:\s*(.+)$/m)?.[1]?.trim(); const data = block.match(/^data:\s*(.+)$/m)?.[1]?.trim()
        if (!type || !data) return
        if (type === 'progress') setProgress(previous => [...previous, JSON.parse(data) as Progress])
        if (type === 'plan_updated') setPlan(JSON.parse(data) as Plan)
        if (type === 'message_completed') finalMessage = JSON.parse(data) as Message
        if (type === 'error') { const issue = JSON.parse(data) as { message?: string }; throw new Error(issue.message || '这次音乐对话暂时无法完成') }
      }
      while (true) {
        const { value, done } = await reader.read(); buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done })
        let boundary: number
        while ((boundary = buffer.indexOf('\n\n')) >= 0) { consume(buffer.slice(0, boundary)); buffer = buffer.slice(boundary + 2) }
        if (done) break
      }
      if (!finalMessage) throw new Error('Agent 未返回最终回复')
      const persisted = await api<Message[]>(`/conversations/${current.id}/messages`)
      setMessages(persisted)
      const launchCard = [...persisted].reverse().find(item => item.type === 'TOURNAMENT_CARD' && item.cardType === 'WORLD_CUP_LAUNCH')
      if (launchCard) {
        try { setTournamentPrompt(JSON.parse(launchCard.cardPayloadJson || '{}').preferenceText || content) } catch { setTournamentPrompt(content) }
      }
      const refreshed = await api<Conversation[]>(`/conversations`)
      setConversations(refreshed); setCurrent(refreshed.find(item => item.id === current.id) ?? current)
    } catch (error) {
      setMessages(previous => previous.filter(item => item.id !== clientId))
      setDraft(content); setNotice(friendlyError(error))
    } finally { setPending(false); setCollapsed(true) }
  }

  const displayMessages = useMemo(() => messages.filter(message => message.type !== 'AGENT_RUN'), [messages])
  function beginTournament(size: 16 | 32, preferenceText?: string) {
    const prompt = preferenceText || tournamentPrompt
    if (!prompt) return
    sessionStorage.setItem('isq-worldcup-preference', prompt)
    sessionStorage.setItem('isq-worldcup-size', String(size))
    if (current) sessionStorage.setItem('isq-conversation-id', current.id)
    window.location.hash = 'worldcup'
  }
  function resumeTournament(tournamentId: string) { sessionStorage.setItem('isq-open-tournament-id', tournamentId); window.location.hash = 'worldcup' }
  return <main className="conversation-shell">
    <aside className="conversation-sidebar">
      <a className="conversation-brand" href="#worldcup">INDIESOUNDQUEST <small>歌曲世界杯</small></a>
      <button className="new-conversation" onClick={() => void createConversation()} disabled={pending}>新建探索</button>
      <p className="conversation-label">最近探索</p>
      <nav>{conversations.map(conversation => <button key={conversation.id} onClick={() => void open(conversation)} className={conversation.id === current?.id ? 'active' : ''}><strong>{conversation.title}</strong><small>{new Date(conversation.lastMessageAt).toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' })}</small></button>)}</nav>
    </aside>
    <section className="conversation-main">
      <header className="conversation-header"><p className="eyebrow">音乐探索对话</p><h1>{current?.title || '新的音乐探索'}</h1><p>聊聊喜欢的声音、创作背景或下一场想开始的歌曲世界杯。</p></header>
      {notice && <p className="conversation-notice">{notice}</p>}
      <section className="conversation-timeline" ref={timelineRef} aria-live="polite">
        {displayMessages.length === 0 && <div className="conversation-welcome"><p className="eyebrow">歌曲世界杯</p><h2>我们的歌曲世界杯应该从哪开始？</h2><p>描述一下你的音乐喜好，或告诉我最近反复听的歌。我会先和你一起澄清方向、构建候选池，再把它们放进一场比赛。</p><div><button onClick={() => setDraft('我喜欢徐佳莹、艾怡良和郑宜农，想做一场适合深夜的歌曲世界杯。')}>从华语创作女声开始</button><button onClick={() => setDraft('想做一场适合夜晚散步的独立流行歌曲世界杯。')}>寻找夜晚散步的歌</button></div></div>}
        {displayMessages.map(message => message.type === 'TOURNAMENT_CARD' ? (message.cardType === 'WORLD_CUP_LAUNCH' ? <TournamentLaunchCard key={message.id} payload={message.cardPayloadJson} onStart={beginTournament} /> : <TournamentStatusCard key={message.id} payload={message.cardPayloadJson} onOpen={resumeTournament} />) : message.type === 'CANDIDATE_POOL_CARD' ? <CandidatePoolCard key={message.id} payload={message.cardPayloadJson} onStart={beginTournament} /> : message.type === 'REPORT_CARD' ? <ReportCard key={message.id} payload={message.cardPayloadJson} onOpen={resumeTournament} /> : <article className={`conversation-message ${message.role === 'USER' ? 'user' : 'agent'}`} key={message.id}><p>{message.content}</p></article>)}
        {(pending || plan || progress.length > 0) && <AgentRun plan={plan} progress={progress} collapsed={collapsed} active={pending} onToggle={() => setCollapsed(value => !value)} />}
      </section>
      <form className="conversation-composer" onSubmit={event => void send(event)}><textarea value={draft} onChange={event => setDraft(event.target.value)} placeholder="说说你想探索的音乐…" maxLength={2000} disabled={!current || pending}/><button type="submit" disabled={!draft.trim() || !current || pending}>{pending ? '正在整理…' : '发送'}</button></form>
    </section>
  </main>
}

function AgentRun({ plan, progress, collapsed, active, onToggle }: { plan: Plan | null; progress: Progress[]; collapsed: boolean; active: boolean; onToggle: () => void }) {
  const latest = progress[progress.length - 1]
  return <aside className={`conversation-run ${collapsed ? 'collapsed' : ''}`}><button onClick={onToggle}><span>{active ? '正在整理这次音乐探索' : '已完成本次音乐探索'}</span><small>{latest?.message || plan?.summary}</small><span>{collapsed ? '展开' : '收起'}</span></button>{!collapsed && <>{plan && <ol className="conversation-plan">{plan.items.map(item => <li key={item.id} className={item.status}><span>{item.title}</span><small>{item.detail}</small></li>)}</ol>}{progress.length > 0 && <ol className="conversation-progress">{progress.map((item, index) => <li key={`${item.phase}-${index}`}>{item.message}</li>)}</ol>}</>}</aside>
}

function TournamentLaunchCard({ payload, onStart }: { payload?: string | null; onStart: (size: 16 | 32, preferenceText?: string) => void }) {
  let title = '把这轮偏好放进一场比赛'
  let preferenceText = ''
  try { const value = JSON.parse(payload || '{}'); title = value.title || title; preferenceText = value.preferenceText || '' } catch { /* persisted card remains usable without its optional title */ }
  return <section className="tournament-launch-card"><p className="eyebrow">歌曲世界杯</p><h2>{title}</h2><p>候选歌曲会由音乐探索 Agent 重新检索与核验；你确认后才会开始两两对决。</p><div><button onClick={() => onStart(16, preferenceText)}>用这轮偏好开始 16 首比赛</button><button onClick={() => onStart(32, preferenceText)}>开始 32 首比赛</button></div></section>
}

function CandidatePoolCard({ payload, onStart }: { payload?: string | null; onStart: (size:16|32, preferenceText?:string)=>void }) { let value: { size?: 16|32; status?: string; summary?: string; preferenceText?: string } = {}; try { value = JSON.parse(payload || '{}') } catch {} return <section className="tournament-launch-card"><p className="eyebrow">候选歌曲池</p><h2>{value.status === 'ready_for_confirmation' ? `已为 ${value.size} 首赛事整理候选` : '候选池需要继续调整'}</h2><p>{value.summary || '候选池已保存；进入世界杯页面可以继续确认与开赛。'}</p>{value.preferenceText && <div><button onClick={()=>onStart(value.size || 16, value.preferenceText)}>进入候选确认与开赛</button></div>}</section> }
function TournamentStatusCard({ payload, onOpen }: { payload?: string | null; onOpen:(id:string)=>void }) { let value: { tournamentId?: string; size?: number; status?: string } = {}; try { value = JSON.parse(payload || '{}') } catch {} return <section className="tournament-launch-card"><p className="eyebrow">赛事已创建</p><h2>{value.size || 16} 首歌曲世界杯</h2><p>{value.status === 'READY' ? '赛程已准备完成。你可以继续在世界杯页面完成每一轮选择。' : '赛事状态已保存。'}</p>{value.tournamentId && <div><button onClick={()=>onOpen(value.tournamentId!)}>进入这场比赛</button></div>}</section> }
function ReportCard({ payload, onOpen }: { payload?: string | null; onOpen:(id:string)=>void }) { let value: { tournamentId?: string; status?: string; championTitle?: string } = {}; try { value = JSON.parse(payload || '{}') } catch {} return <section className="tournament-launch-card"><p className="eyebrow">赛后偏好报告</p><h2>{value.championTitle ? `冠军《${value.championTitle}》的探索报告` : '本场音乐探索报告'}</h2><p>{value.status === 'READY' ? '报告已完成。你可以继续追问本场的选择轨迹与推荐方向。' : '报告正在生成中。'}</p>{value.tournamentId && <div><button onClick={()=>onOpen(value.tournamentId!)}>{value.status === 'READY' ? '查看完整报告' : '查看赛事进度'}</button></div>}</section> }
