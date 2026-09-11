import { FormEvent, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import type { CandidateItem, CandidatePoolResponse } from './candidatePool'
import { enqueueAgentRun, followAgentRun, progressMetricsText, publicProgress, type AgentRunEvent, type AgentRunPlan, type AgentRunProgress } from './agentRunStream'
import { playbackLabel, useListeningPreview } from './listening'
import './conversation.css'
import './agentWorkspace.css'
import './tournamentLaunch.css'
import './conversationWorldcup.css'

type Conversation = { id: string; title: string; summary?: string | null; summaryVersion?: number; summaryThroughSequence?: number; summaryUpdatedAt?: string | null; status: 'ACTIVE' | 'ARCHIVED' | 'DELETED'; lastMessageAt: string }
type MemoryStatus = { estimatedTokens: number; tokenThreshold: number; workingContextBudget: number; providerContextWindow: number; percent: number; unsummarizedTurns: number; turnThreshold: number; retainedRecentTurns: number; autoCompressionReady: boolean; manualCompressionAvailable: boolean; summaryVersion: number; summaryThroughSequence: number; lastCompressedAt?: string | null }
type Message = { id: string; agentRunId?: string | null; role: 'USER' | 'ASSISTANT' | 'SYSTEM'; type: 'USER_TEXT' | 'AGENT_TEXT' | 'AGENT_RUN' | 'SYSTEM_NOTE' | 'TOURNAMENT_CARD' | 'CANDIDATE_POOL_CARD' | 'REPORT_CARD' | 'CLARIFICATION_CARD' | 'RECOMMENDATION_CARD'; content?: string | null; cardType?: string | null; cardPayloadJson?: string | null; status: 'RUNNING' | 'COMPLETED' | 'FAILED'; sequenceNumber: number; createdAt: string }
type RunEvent = AgentRunEvent
type Progress = AgentRunProgress
type Plan = AgentRunPlan
type CardItem = Pick<CandidateItem, 'recordingId' | 'title' | 'artistName' | 'coverUrl'>
type CardPayload = { size?: 16 | 32; status?: string; summary?: string; preferenceText?: string; items?: CardItem[] }
type Tournament = { id: string; status: string; size: number; completedVoteCount: number; currentMatch: { id: string; leftEntryId: string; rightEntryId: string } | null; entries: { id: string; title: string; artistName: string; coverUrl?: string }[] }
type Report = { runId?: string; status: string; reportId?: string; version?: number; report?: { summary?: string; preferenceDimensions?: { label?: string; summary?: string }[]; songRecommendations?: { title?: string; artistName?: string; reason?: string; searchUrl?: string }[]; artistRecommendations?: { artistName?: string; reason?: string; searchUrl?: string }[]; personalityEasterEgg?: string } }

async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api/v1${path}`, { credentials: 'include', ...options })
  if (!response.ok) throw new Error(`请求失败（${response.status}）`)
  return response.json() as Promise<T>
}

async function apiVoid(path: string, options: RequestInit = {}): Promise<void> {
  const response = await fetch(`/api/v1${path}`, { credentials: 'include', ...options })
  if (!response.ok) throw new Error(`请求失败（${response.status}）`)
}

function friendlyError(error: unknown) {
  const text = error instanceof Error ? error.message : ''
  if (/load failed|failed to fetch|networkerror/i.test(text)) return '连接意外中断了，请检查网络后重试。'
  return text || '这次音乐对话暂时无法完成，请稍后重试。'
}

async function createAndWaitForReport(tournamentId: string, onProgress: (item: Progress) => void, onPlan: (item: Plan) => void, onRunId?: (runId: string | null) => void): Promise<Report> {
  let report = await api<Report>(`/tournaments/${tournamentId}/preference-report`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Request-Id': crypto.randomUUID() }, body: JSON.stringify({ force: false }) })
  if (report.status === 'FAILED') throw new Error('报告生成失败，请稍后重试。')
  if (report.status !== 'READY' && report.runId) { onRunId?.(report.runId); try { await followAgentRun(report.runId, { onProgress, onPlan }) } finally { onRunId?.(null) } }
  report = await api<Report>(`/tournaments/${tournamentId}/preference-report`)
  if (report.status !== 'READY') throw new Error('报告仍在处理中，请稍后重试。')
  return report
}

async function queuedRun<T>(path:string,options:RequestInit,onProgress:(item:Progress)=>void,onPlan:(item:Plan)=>void,onRunId?:(runId:string|null)=>void):Promise<T>{const queued=await enqueueAgentRun(path,options);onRunId?.(queued.runId);try{const completed=await followAgentRun<T>(queued.runId,{onProgress,onPlan});if(completed.status==='CANCELLED')throw new Error('已停止本轮任务');if(!completed.result)throw new Error('本轮没有返回可用结果');return completed.result}finally{onRunId?.(null)}}

export function ConversationApp() {
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [current, setCurrent] = useState<Conversation | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [draft, setDraft] = useState('')
  const [pending, setPending] = useState(false)
  const [progress, setProgress] = useState<Progress[]>([])
  const [plan, setPlan] = useState<Plan | null>(null)
  const [planHistory, setPlanHistory] = useState<Plan[]>([])
  const [runElapsedMs, setRunElapsedMs] = useState(0)
  const [collapsed, setCollapsed] = useState(false)
  const [notice, setNotice] = useState('')
  const [candidateRun, setCandidateRun] = useState<{ size: 16 | 32; preferenceText: string } | null>(null)
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false)
  const [conversationMenuId, setConversationMenuId] = useState<string | null>(null)
  const [renamingId, setRenamingId] = useState<string | null>(null)
  const [renameDraft, setRenameDraft] = useState('')
  const [deleteConfirmId, setDeleteConfirmId] = useState<string | null>(null)
  const [currentRunId, setCurrentRunId] = useState<string | null>(null)
  const [conversationSearch, setConversationSearch] = useState('')
  const [showArchived, setShowArchived] = useState(false)
  const [regenerationOpen, setRegenerationOpen] = useState(false)
  const [regenerationInstruction, setRegenerationInstruction] = useState('')
  const [memory, setMemory] = useState<MemoryStatus | null>(null)
  const [compressingMemory, setCompressingMemory] = useState(false)
  const timelineRef = useRef<HTMLElement>(null)
  const resumingRunId = useRef<string | null>(null)
  const runStartedAt = useRef<number | null>(null)
  const wasRunActive = useRef(false)

  useEffect(() => { void boot() }, [])
  useEffect(() => { timelineRef.current?.scrollTo({ top: timelineRef.current.scrollHeight, behavior: 'smooth' }) }, [messages, pending, candidateRun])

  function recordPlan(next: Plan) {
    setPlan(next)
    setPlanHistory(previous => previous.some(item => item.revision === next.revision) ? previous.map(item => item.revision === next.revision ? next : item) : [...previous, next].slice(-8))
  }

  async function refreshMessages(conversationId = current?.id) {
    if (!conversationId) return
    const loaded = await api<Message[]>(`/conversations/${conversationId}/messages`)
    setMessages(loaded)
    const latestRun = [...loaded].reverse().find(message => message.type === 'AGENT_RUN' && message.agentRunId)
    if (latestRun?.agentRunId) await replayRunEvents(latestRun.agentRunId)
  }
  async function refreshMemory(conversationId = current?.id) {
    if (!conversationId) return
    setMemory(await api<MemoryStatus>(`/conversations/${conversationId}/memory`))
  }
  async function replayRunEvents(runId: string) {
    try {
      const payload = await api<{ runStatus: string; events: RunEvent[] }>(`/agent-runs/${runId}/events`)
      const replayProgress: Progress[] = []
      const replayPlans: Plan[] = []
      let cursor = 0
      for (const event of payload.events) {
        cursor = Math.max(cursor, event.sequenceNumber)
        const data = JSON.parse(event.payloadJson) as Record<string, unknown>
        const item = publicProgress(event, data)
        if (item) replayProgress.push(item)
        if (event.type === 'PLAN_UPDATED') replayPlans.push(data as unknown as Plan)
      }
      if (replayProgress.length) setProgress(replayProgress)
      if (replayPlans.length) { setPlanHistory(replayPlans.slice(-8)); setPlan(replayPlans[replayPlans.length - 1]) }
      if (payload.runStatus === 'RUNNING' || payload.runStatus === 'QUEUED') {
        setCurrentRunId(runId)
        setCollapsed(false)
        if (resumingRunId.current !== runId) {
          resumingRunId.current = runId; setPending(true)
          void followAgentRun(runId, { onProgress: item => setProgress(previous => [...previous, item]), onPlan: recordPlan }, 15 * 60_000, cursor)
            .then(() => refreshMessages())
            .catch(error => setNotice(friendlyError(error)))
            .finally(() => { resumingRunId.current = null; setCurrentRunId(null); setPending(false); setCollapsed(true) })
        }
      } else { setCurrentRunId(null); if (payload.runStatus === 'WAITING_FOR_USER') setCollapsed(false); else setCollapsed(true) }
    } catch { /* replay is best-effort */ }
  }
  async function boot() {
    try { const list = await api<Conversation[]>('/conversations'); setConversations(list); if (list[0]) await open(list[0]); else await createConversation() } catch (error) { setNotice(friendlyError(error)) }
  }
  async function createConversation() {
    try { const created = await api<Conversation>('/conversations', { method: 'POST' }); setConversations(previous => [created, ...previous]); setCurrent(created); setMessages([]); setProgress([]); setPlan(null); setPlanHistory([]); setCurrentRunId(null); setNotice(''); setMobileSidebarOpen(false); await refreshMemory(created.id) } catch (error) { setNotice(friendlyError(error)) }
  }
  async function open(conversation: Conversation) {
    if (pending || candidateRun) return
    try { setProgress([]); setPlan(null); setPlanHistory([]); await Promise.all([refreshMessages(conversation.id), refreshMemory(conversation.id)]); setCurrent(conversation); setNotice(''); setMobileSidebarOpen(false); setConversationMenuId(null) } catch (error) { setNotice(friendlyError(error)) }
  }
  async function renameConversation(conversation: Conversation) {
    const title = renameDraft.trim()
    if (!title || title === conversation.title) { setRenamingId(null); return }
    try {
      const updated = await api<Conversation>(`/conversations/${conversation.id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title }) })
      setConversations(previous => previous.map(item => item.id === updated.id ? updated : item))
      if (current?.id === updated.id) setCurrent(updated)
      setRenamingId(null); setConversationMenuId(null)
    } catch (error) { setNotice(friendlyError(error)) }
  }
  async function deleteConversation(conversation: Conversation) {
    try {
      await apiVoid(`/conversations/${conversation.id}`, { method: 'DELETE' })
      const remaining = conversations.filter(item => item.id !== conversation.id)
      setConversations(remaining); setDeleteConfirmId(null); setConversationMenuId(null)
      if (current?.id === conversation.id) {
        setCurrent(null); setMessages([]); setProgress([]); setPlan(null); setPlanHistory([])
        if (remaining[0]) await open(remaining[0]); else await createConversation()
      }
    } catch (error) { setNotice(friendlyError(error)) }
  }
  async function setArchived(conversation: Conversation, archived: boolean) {
    try {
      await apiVoid(`/conversations/${conversation.id}/${archived ? 'archive' : 'restore'}`, { method: 'POST' })
      const refreshed = await api<Conversation[]>('/conversations'); setConversations(refreshed); setConversationMenuId(null)
      if (current?.id === conversation.id) setCurrent(refreshed.find(item => item.id === conversation.id) ?? null)
    } catch (error) { setNotice(friendlyError(error)) }
  }
  async function persistCard(type: 'CANDIDATE_POOL_CARD' | 'TOURNAMENT_CARD' | 'REPORT_CARD', cardType: string, payload: object) {
    if (!current) return
    await api(`/conversations/${current.id}/cards`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID() }, body: JSON.stringify({ type, cardType, payloadJson: JSON.stringify(payload) }) })
    await refreshMessages()
  }
  async function send(event: FormEvent) {
    event.preventDefault(); await sendContent(draft.trim())
  }
  async function sendContent(content: string) {
    if (!content || !current || pending || candidateRun) return
    const clientId = crypto.randomUUID(); const optimistic: Message = { id: clientId, role: 'USER', type: 'USER_TEXT', content, status: 'COMPLETED', sequenceNumber: Date.now(), createdAt: new Date().toISOString() }
    setMessages(previous => [...previous, optimistic]); setDraft(''); setPending(true); setProgress([]); setPlan(null); setPlanHistory([]); setCollapsed(false); setNotice('')
    try {
      const queued = await enqueueAgentRun(`/conversations/${current.id}/messages`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': clientId }, body: JSON.stringify({ content }) })
      setCurrentRunId(queued.runId)
      await followAgentRun(queued.runId, { onProgress: item => setProgress(previous => [...previous, item]), onPlan: recordPlan })
      await Promise.all([refreshMessages(), refreshMemory()]); const refreshed = await api<Conversation[]>('/conversations'); setConversations(refreshed); setCurrent(refreshed.find(item => item.id === current.id) ?? current)
    } catch (error) { setMessages(previous => previous.filter(item => item.id !== clientId)); setDraft(content); setNotice(friendlyError(error)) } finally { setCurrentRunId(null); setPending(false); setCollapsed(true) }
  }
  async function compressConversationMemory() {
    if (!current || runActive || compressingMemory || !memory?.manualCompressionAvailable) return
    setCompressingMemory(true); setNotice('')
    try {
      const updated = await api<MemoryStatus>(`/conversations/${current.id}/memory:compress`, { method: 'POST' })
      setMemory(updated)
      const refreshed = await api<Conversation[]>('/conversations'); setConversations(refreshed); setCurrent(refreshed.find(item => item.id === current.id) ?? current)
      setNotice(`较早的对话已整理，最近 ${updated.retainedRecentTurns} 轮仍保留原文。`)
    } catch (error) { setNotice(friendlyError(error)) } finally { setCompressingMemory(false) }
  }
  async function stopCurrentRun() {
    if (!currentRunId) return
    try { await apiVoid(`/agent-runs/${currentRunId}/cancel`, { method: 'POST' }); setNotice('已停止本轮任务。'); await refreshMessages() } catch (error) { setNotice(friendlyError(error)) }
  }
  function regenerateLastAnswer(instruction = '') {
    const lastAgentIndex = messages.map(message => message.role === 'ASSISTANT' && message.type === 'AGENT_TEXT').lastIndexOf(true)
    const lastUser = [...messages.slice(0, lastAgentIndex < 0 ? messages.length : lastAgentIndex)].reverse().find(message => message.role === 'USER' && message.type === 'USER_TEXT' && message.content?.trim())
    if (lastUser?.content) { setRegenerationOpen(false); setRegenerationInstruction(''); void sendContent(`请重新生成上一轮回答。原始请求是：${lastUser.content}${instruction.trim() ? `\n补充要求：${instruction.trim()}` : ''}`) }
  }
  function startCandidateRun(size: 16 | 32, preferenceText: string) {
    if (!preferenceText.trim()) { setNotice('先描述一下你想放进世界杯的音乐偏好。'); return }
    setCandidateRun({ size, preferenceText }); setProgress([]); setPlan(null); setPlanHistory([]); setCollapsed(false); setNotice('')
  }
  async function requestExplorationReport() {
    if (!current || pending || candidateRun) return
    setPending(true); setProgress([]); setPlan(null); setPlanHistory([]); setCollapsed(false); setNotice('')
    try {
      const queued = await enqueueAgentRun(`/conversations/${current.id}/exploration-report`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID() } })
      setCurrentRunId(queued.runId)
      await followAgentRun(queued.runId, { onProgress: item => setProgress(previous => [...previous, item]), onPlan: recordPlan })
      await refreshMessages(); const refreshed = await api<Conversation[]>('/conversations'); setConversations(refreshed); setCurrent(refreshed.find(item => item.id === current.id) ?? current)
    } catch (error) { setNotice(friendlyError(error)) } finally { setCurrentRunId(null); setPending(false); setCollapsed(true) }
  }
  const displayMessages = useMemo(() => messages.filter(message => message.type !== 'AGENT_RUN'), [messages])
  const visibleConversations = useMemo(() => conversations.filter(item => (showArchived ? item.status === 'ARCHIVED' : item.status === 'ACTIVE') && item.title.toLocaleLowerCase().includes(conversationSearch.trim().toLocaleLowerCase())), [conversations, showArchived, conversationSearch])
  const runActive = pending || Boolean(candidateRun)
  const hasRunState = runActive || Boolean(plan) || progress.length > 0
  useEffect(() => {
    if (runActive && !wasRunActive.current) { runStartedAt.current = Date.now(); setRunElapsedMs(0) }
    wasRunActive.current = runActive
    if (!runActive || !runStartedAt.current) return
    const update = () => setRunElapsedMs(Date.now() - (runStartedAt.current || Date.now()))
    update(); const timer = window.setInterval(update, 250)
    return () => window.clearInterval(timer)
  }, [runActive])
  return <main className="conversation-shell">
    {mobileSidebarOpen && <button className="conversation-sidebar-scrim" type="button" aria-label="关闭会话列表" onClick={() => setMobileSidebarOpen(false)} />}
    <aside className={`conversation-sidebar ${mobileSidebarOpen ? 'open' : ''}`}>
      <div className="conversation-brand"><BrandMark /><span><strong>IndieSoundQuest</strong><small>音乐探索</small></span></div>
      <button className="new-conversation" onClick={() => void createConversation()} disabled={runActive}><PlusIcon /><span>新建对话</span></button>
      <div className="conversation-list-tools"><input value={conversationSearch} onChange={event => setConversationSearch(event.target.value)} placeholder="搜索对话" aria-label="搜索对话" /><button type="button" className={showArchived ? 'active' : ''} onClick={() => setShowArchived(value => !value)}>{showArchived ? '返回最近' : '归档'}</button></div>
      <p className="conversation-label">{showArchived ? '已归档对话' : '最近对话'}</p>
      <nav>{visibleConversations.map(conversation => <div className={`conversation-nav-item ${conversation.id === current?.id ? 'active' : ''}`} key={conversation.id}>{renamingId === conversation.id ? <form className="conversation-rename" onSubmit={event => { event.preventDefault(); void renameConversation(conversation) }}><input autoFocus maxLength={80} value={renameDraft} onChange={event => setRenameDraft(event.target.value)} onBlur={() => void renameConversation(conversation)} aria-label="对话名称" /></form> : <button className="conversation-nav-open" onClick={() => void open(conversation)}><span className="conversation-nav-icon" aria-hidden="true" /><span><strong>{conversation.title}</strong><small>{new Date(conversation.lastMessageAt).toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' })}</small></span></button>}<button className="conversation-nav-more" type="button" aria-label={`管理对话：${conversation.title}`} onClick={() => setConversationMenuId(value => value === conversation.id ? null : conversation.id)}><MoreIcon /></button>{conversationMenuId === conversation.id && <div className="conversation-nav-menu">{deleteConfirmId === conversation.id ? <><p>删除这段对话？</p><div><button type="button" onClick={() => setDeleteConfirmId(null)}>取消</button><button className="danger" type="button" onClick={() => void deleteConversation(conversation)}>删除</button></div></> : <><button type="button" onClick={() => { setRenameDraft(conversation.title); setRenamingId(conversation.id); setConversationMenuId(null) }}>重命名</button><button type="button" onClick={() => void setArchived(conversation, conversation.status !== 'ARCHIVED')}>{conversation.status === 'ARCHIVED' ? '恢复对话' : '归档对话'}</button><button className="danger" type="button" onClick={() => setDeleteConfirmId(conversation.id)}>删除对话</button></>}</div>}</div>)}</nav>
      <div className="conversation-sidebar-status"><span /><small>随时可以开始</small></div>
    </aside>
    <section className="conversation-main"><header className="conversation-header"><button className="mobile-menu-toggle" type="button" aria-label="打开会话列表" onClick={() => setMobileSidebarOpen(true)}><MenuIcon /></button><div><p className="eyebrow">当前对话</p><h1>{current?.title || '新的音乐探索'}</h1></div><button className="report-action" type="button" disabled={!current || runActive} onClick={() => void requestExplorationReport()}><ReportIcon /><span>生成报告</span></button></header>{notice && <p className="conversation-notice">{notice}</p>}
      <section className="conversation-timeline" ref={timelineRef} aria-live="polite">
        {displayMessages.length === 0 && <Welcome onSelect={(text) => setDraft(text)} />}
        {displayMessages.map((message, index) => {
          let content: ReactNode
          if (message.type === 'TOURNAMENT_CARD' && message.cardType === 'WORLD_CUP_LAUNCH') content = <TournamentLaunchCard payload={message.cardPayloadJson} onStart={startCandidateRun} />
          else if (message.type === 'CANDIDATE_POOL_CARD') content = <CandidatePoolCard cardMessageId={message.id} alreadyStarted={displayMessages.some(candidateTournamentCardFor(message.id)) || legacyCandidateHasFollowingTournament(displayMessages, index)} payload={message.cardPayloadJson} onRebuild={startCandidateRun} onTournamentCreated={(tournamentId, size, sourceCandidateMessageId) => void persistCard('TOURNAMENT_CARD', 'TOURNAMENT', { tournamentId, size, status: 'READY', sourceCandidateMessageId })} />
          else if (message.type === 'CLARIFICATION_CARD') content = <ClarificationCard payload={message.cardPayloadJson} agentRunId={findWaitingRunId(messages, messages.findIndex(item => item.id === message.id))} onCompleted={() => void refreshMessages()} onProgress={(item) => setProgress(previous => [...previous, item])} onPlan={recordPlan} />
          else if (message.type === 'RECOMMENDATION_CARD' && message.cardType === 'MUSIC_RECOMMENDATIONS') content = <MusicRecommendationsCard payload={message.cardPayloadJson} conversationId={current?.id} />
          else if (message.type === 'RECOMMENDATION_CARD' && message.cardType === 'PUBLIC_MUSIC_SOURCES') content = <PublicMusicSourcesCard payload={message.cardPayloadJson} />
          else if (message.type === 'RECOMMENDATION_CARD') content = <RecommendationNoticeCard payload={message.cardPayloadJson} />
          else if (message.type === 'TOURNAMENT_CARD') content = <TournamentStatusCard payload={message.cardPayloadJson} onProgress={item => setProgress(previous => [...previous, item])} onPlan={recordPlan} onRunActive={active => { setPending(active); if (active) { setProgress([]); setPlan(null); setPlanHistory([]); setCollapsed(false) } else setCollapsed(true) }} onReportReady={(report, tournamentId, championTitle) => void persistCard('REPORT_CARD', 'PREFERENCE_REPORT', { tournamentId, reportId: report.reportId, version: report.version, status: report.status, championTitle })} />
          else if (message.type === 'REPORT_CARD' && message.cardType === 'EXPLORATION_REPORT') content = <ExplorationReportCard payload={message.cardPayloadJson} conversationId={current?.id} />
          else if (message.type === 'REPORT_CARD') content = <ReportCard payload={message.cardPayloadJson} />
          else content = <article className={`conversation-message ${message.role === 'USER' ? 'user' : 'agent'}`}>{message.role !== 'USER' && <span className="message-agent-mark"><BrandMark /></span>}<MessageBody content={message.content || ''} /></article>
          return <TimedMessage key={message.id} createdAt={message.createdAt}>{content}</TimedMessage>
        })}
        {candidateRun && <CandidateGenerationCard size={candidateRun.size} preferenceText={candidateRun.preferenceText} onProgress={(item) => setProgress(previous => [...previous, item])} onPlan={recordPlan} onRunId={setCurrentRunId} onCompleted={async payload => { await persistCard('CANDIDATE_POOL_CARD', 'CANDIDATE_POOL', payload); setCandidateRun(null); setCollapsed(true) }} onFailed={message => { setNotice(message); setCandidateRun(null); setCollapsed(true) }} />}
        {hasRunState && <AgentActivity progress={progress} collapsed={collapsed} active={runActive} elapsedMs={runElapsedMs} canStop={Boolean(currentRunId)} canRegenerate={messages.some(message => message.role === 'USER' && message.type === 'USER_TEXT')} onToggle={() => setCollapsed(value => !value)} onStop={() => void stopCurrentRun()} onRegenerate={() => setRegenerationOpen(value => !value)} />}
        {regenerationOpen && !runActive && <section className="regeneration-panel"><strong>重新生成这轮回答</strong><p>可直接重试，也可以附加一个调整方向。</p><div>{['更冷门一些', '减少相同艺人', '换一批歌曲'].map(item => <button type="button" key={item} onClick={() => setRegenerationInstruction(item)}>{item}</button>)}</div><textarea value={regenerationInstruction} onChange={event => setRegenerationInstruction(event.target.value)} placeholder="例如：更冷门一些，减少相同艺人" /><footer><button type="button" onClick={() => setRegenerationOpen(false)}>取消</button><button type="button" className="primary" onClick={() => regenerateLastAnswer(regenerationInstruction)}>重新生成</button></footer></section>}
      </section>
      <form className="conversation-composer" onSubmit={event => void send(event)}><div className="composer-frame"><textarea value={draft} onChange={event => setDraft(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); event.currentTarget.form?.requestSubmit() } }} placeholder="和 IndieSoundQuest 聊聊音乐……" maxLength={2000} disabled={!current || runActive} /><div className="composer-toolbar"><div className="composer-memory"><button className="memory-compress-button" type="button" aria-label="整理较早的对话" title={memory?.manualCompressionAvailable ? '整理较早的对话' : '目前无需整理'} disabled={!memory?.manualCompressionAvailable || runActive || compressingMemory} onClick={() => void compressConversationMemory()}>{compressingMemory ? <span className="memory-spinner" /> : <MemoryIcon />}</button>{memory && <div className="memory-meter" title="当前对话的记忆占用；较长时会自动整理较早内容" role="progressbar" aria-label="对话记忆占用" aria-valuemin={0} aria-valuemax={100} aria-valuenow={memory.percent}><span><i style={{ width: `${memory.percent}%` }} /></span></div>}</div><button className="composer-send" type="submit" aria-label="发送消息" title="发送" disabled={!draft.trim() || !current || runActive}>{pending ? <span className="composer-spinner" /> : <SendIcon />}</button></div></div><small>{memory?.autoCompressionReady ? '稍后会自动整理较早的对话。' : '请核对重要的音乐资料与链接。'}</small></form>
    </section>
    <aside className="conversation-agent-panel"><AgentPlanPanel plans={planHistory.length ? planHistory : (plan ? [plan] : [])} active={runActive} latest={progress[progress.length - 1]} /></aside>
  </main>
}

function Welcome({ onSelect }: { onSelect: (text: string) => void }) { return <div className="conversation-welcome"><p className="eyebrow">音乐探索对话</p><h2>最近想从哪种声音开始？</h2><p>描述喜欢的艺人、反复听的歌、某种情绪或一个聆听场景。我会和你一起理解偏好、查找资料、推荐作品，并把重要线索留在这段对话里。</p><div><button onClick={() => onSelect('我喜欢徐佳莹、艾怡良和郑宜农，想了解她们的共同点和相近的音乐。')}>从华语创作女声开始</button><button onClick={() => onSelect('想找一些适合夜晚散步、克制但不阴郁的独立流行。')}>寻找夜晚散步的歌</button></div></div> }
export function candidateTournamentCardFor(candidateMessageId: string) { return (message: Message) => { if (message.type !== 'TOURNAMENT_CARD') return false; try { return JSON.parse(message.cardPayloadJson || '{}').sourceCandidateMessageId === candidateMessageId } catch { return false } } }
export function legacyCandidateHasFollowingTournament(messages: Message[], candidateIndex: number) { const following = messages.slice(candidateIndex + 1); const nextCandidateIndex = following.findIndex(message => message.type === 'CANDIDATE_POOL_CARD'); return following.slice(0, nextCandidateIndex < 0 ? following.length : nextCandidateIndex).some(message => message.type === 'TOURNAMENT_CARD') }
export function findWaitingRunId(messages: Message[], cardIndex: number) {
  if (cardIndex < 0) return null
  for (let index = cardIndex; index >= 0; index -= 1) {
    const message = messages[index]
    if (message.type === 'AGENT_RUN' && message.agentRunId) return message.agentRunId
  }
  return null
}
function AgentActivity({ progress, collapsed, active, elapsedMs, canStop, canRegenerate, onToggle, onStop, onRegenerate }: { progress: Progress[]; collapsed: boolean; active: boolean; elapsedMs: number; canStop: boolean; canRegenerate: boolean; onToggle: () => void; onStop: () => void; onRegenerate: () => void }) { const latest = progress[progress.length - 1]; return <section className={`agent-activity ${collapsed ? 'collapsed' : ''}`}>{(active || elapsedMs > 0) && <div className={`agent-turn-timer ${active ? 'active' : ''}`}><i aria-hidden="true" /><span>{active ? '正在处理' : '本轮用时'} · {formatElapsed(elapsedMs)}</span></div>}<button type="button" onClick={onToggle}><span className={`agent-activity-indicator ${active ? 'active' : ''}`}>{active ? <span className="agent-thinking-wave" aria-hidden="true"><i /><i /><i /><i /><i /></span> : <BrandMark />}</span><span><strong>{active ? 'IndieSoundQuest 正在工作' : '本轮处理已完成'}</strong><small>{latest?.message ? friendlyRuntimeCopy(latest.message) : (active ? '正在准备下一步' : '过程记录已收起')}</small></span><span className="agent-activity-toggle">{collapsed ? '查看过程' : '收起'}</span></button>{!collapsed && progress.length > 0 && <ol>{progress.map((item, index) => <li key={`${item.phase}-${index}`} className={item.status}><i /><span>{friendlyRuntimeCopy(item.message)}</span>{progressMetricsText(item) && <small>{progressMetricsText(item)}</small>}</li>)}</ol>}{((active && canStop) || (!active && canRegenerate)) && <div className="agent-activity-actions">{active && canStop ? <button type="button" onClick={onStop}>停止生成</button> : <button type="button" onClick={onRegenerate}>重新生成</button>}</div>}</section> }

function AgentPlanPanel({ plans, active, latest }: { plans: Plan[]; active: boolean; latest?: Progress }) { const newest = plans[plans.length - 1]; const [selectedRevision, setSelectedRevision] = useState<number | null>(null); const shown = plans.find(item => item.revision === selectedRevision) || newest; const viewingHistory = Boolean(shown && newest && shown.revision !== newest.revision); return <div className="agent-plan-panel"><header><p className="eyebrow">执行计划</p><span className={active ? 'running' : ''}>{active ? '运行中' : newest ? '已完成' : '空闲'}</span></header>{shown ? <>{plans.length > 1 && <div className="plan-revisions"><span>计划版本</span>{plans.map(item => <button type="button" className={item.revision === shown.revision ? 'active' : ''} key={item.revision} onClick={() => setSelectedRevision(item.revision)}>v{item.revision}</button>)}{viewingHistory && <button type="button" onClick={() => setSelectedRevision(null)}>回到最新</button>}</div>}<h2>{friendlyRuntimeCopy(shown.summary || '本轮音乐探索')}</h2>{shown.changeSummary && <p>{friendlyRuntimeCopy(shown.changeSummary)}</p>}<ol>{shown.items.map(item => <li key={item.id} className={item.status}><i /><span><strong>{friendlyRuntimeCopy(item.title)}</strong><small>{friendlyRuntimeCopy(item.detail)}</small></span></li>)}</ol>{latest && !viewingHistory && <footer><small>当前状态</small><span>{friendlyRuntimeCopy(latest.message)}</span></footer>}</> : <div className="agent-plan-empty"><span className="plan-empty-mark"><BrandMark /></span><strong>等待新的探索</strong><p>开始对话后，这里会显示接下来的安排与当前进度。</p></div>}</div> }

export function friendlyRuntimeCopy(value: string) {
  return value
    .replace('计划正根据对话内容和工具结果滚动调整。', '会根据对话内容和检索结果调整安排。')
    .replace('已根据最新 ReAct 决策重排公开计划', '已根据最新进展调整安排')
    .replace('结合本轮消息与会话上下文理解需求', '结合本轮对话理解你的需求')
    .replace('请求已进入 Agent 队列', '请求已进入处理队列')
    .replace('Agent 已开始处理', '已开始处理')
    .replace(/\bReAct\b/gi, '')
    .replace(/\bAgent\b/gi, 'IndieSoundQuest')
    .replace(/\s{2,}/g, ' ')
    .trim()
}

function formatElapsed(value: number) { const seconds = Math.max(0, Math.floor(value / 1000)); return seconds < 60 ? `${seconds} 秒` : `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}` }
function formatMessageTime(value: string) { const date = new Date(value); return Number.isNaN(date.getTime()) ? '' : date.toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }) }
function TimedMessage({ createdAt, children }: { createdAt: string; children: ReactNode }) { const label = formatMessageTime(createdAt); return <div className="conversation-message-shell">{label && <time dateTime={createdAt}>{label}</time>}{children}</div> }

function MessageBody({ content }: { content: string }) { const blocks = content.split(/\n{2,}/).filter(Boolean); return <div className="message-body">{blocks.map((block, index) => <p key={index}>{block.split(/(\*\*[^*]+\*\*)/g).map((part, partIndex) => part.startsWith('**') && part.endsWith('**') ? <strong key={partIndex}>{part.slice(2, -2)}</strong> : <span key={partIndex}>{part}</span>)}</p>)}</div> }

function BrandMark() { return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 9v6M9 6v12M13 4v16M17 7v10M21 10v4" /></svg> }
function PlusIcon() { return <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M10 4v12M4 10h12" /></svg> }
function MoreIcon() { return <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="5" cy="12" r="1" /><circle cx="12" cy="12" r="1" /><circle cx="19" cy="12" r="1" /></svg> }
function MenuIcon() { return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M4 12h16M4 17h16" /></svg> }
function SendIcon() { return <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M4 10h11M11 6l4 4-4 4" /></svg> }
function ReportIcon() { return <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M6 3.5h6l3 3V16.5H6zM12 3.5v3h3M8.5 10h4M8.5 13h4" /></svg> }
function MemoryIcon() { return <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M5 6.5C5 5.1 7.2 4 10 4s5 1.1 5 2.5S12.8 9 10 9 5 7.9 5 6.5Zm0 0v3C5 10.9 7.2 12 10 12s5-1.1 5-2.5v-3M5 9.5v3C5 13.9 7.2 15 10 15s5-1.1 5-2.5v-3" /></svg> }
function MusicCover({ url, title }: { url?: string; title?: string }) { const [failed, setFailed] = useState(false); return url && !failed ? <img src={url} alt="" loading="lazy" onError={() => setFailed(true)} /> : <span className="music-cover-placeholder" aria-hidden="true">{(title || 'ISQ').slice(0, 2)}</span> }
function TournamentLaunchCard({ payload, onStart }: { payload?: string | null; onStart: (size: 16 | 32, preferenceText: string) => void }) { let title = '把这轮偏好放进一场比赛'; let preferenceText = ''; try { const value = JSON.parse(payload || '{}'); title = value.title || title; preferenceText = value.preferenceText || '' } catch { /* malformed legacy card */ } return <section className="tournament-launch-card"><p className="eyebrow">歌曲世界杯</p><h2>{title}</h2><p>先查找并核验候选歌曲；你确认后才会开始两两对决。</p><div><button onClick={() => onStart(16, preferenceText)}>构建 16 首候选池</button><button onClick={() => onStart(32, preferenceText)}>构建 32 首候选池</button></div></section> }
function CandidateGenerationCard({ size, preferenceText, onProgress, onPlan, onRunId, onCompleted, onFailed }: { size: 16 | 32; preferenceText: string; onProgress: (item: Progress) => void; onPlan: (item: Plan) => void; onRunId: (runId: string | null) => void; onCompleted: (payload: CardPayload) => Promise<void>; onFailed: (message: string) => void }) { const [status, setStatus] = useState('正在理解你的偏好并寻找歌曲…'); useEffect(() => { let alive = true; const requestId=crypto.randomUUID(); void queuedRun<CandidatePoolResponse>('/agent-runs/candidate-pool', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Request-Id': requestId }, body: JSON.stringify({ size, preferenceText, seedArtistIds: [], confirmedArtists: [] }) }, item => { if (alive) { setStatus(item.message); onProgress(item) } }, onPlan, onRunId).then(async result => { if (!alive) return; if (result.status !== 'ready_for_confirmation' || !result.candidatePool) { onFailed(result.candidatePool?.warnings?.[0]?.message || '候选池暂时不足以开赛，请补充偏好后重试。'); return } await onCompleted({ size, status: result.status, preferenceText, summary: result.candidatePool.candidateSummary, items: result.candidatePool.items.map(item => ({ recordingId: item.recordingId, title: item.title, artistName: item.artistName, coverUrl: item.coverUrl })) }) }).catch(error => alive && onFailed(friendlyError(error))); return () => { alive = false } }, [size, preferenceText]); return <section className="tournament-launch-card"><p className="eyebrow">候选池生成中</p><h2>正在为这场 {size} 首比赛挑选歌曲</h2><p>{status}</p></section> }
function CandidatePoolCard({ cardMessageId, alreadyStarted, payload, onRebuild, onTournamentCreated }: { cardMessageId: string; alreadyStarted: boolean; payload?: string | null; onRebuild: (size: 16 | 32, preferenceText: string) => void; onTournamentCreated: (id: string, size: number, sourceCandidateMessageId: string) => void }) { let value: CardPayload = {}; try { value = JSON.parse(payload || '{}') } catch { /* display degraded card */ } const size = value.size || 16; const [removed, setRemoved] = useState<string[]>([]); const [starting, setStarting] = useState(false); const [created, setCreated] = useState<string | null>(null); const [error, setError] = useState(''); const active = (value.items || []).filter(item => !removed.includes(item.recordingId)).slice(0, size); const remove = (id: string) => setRemoved(current => current.includes(id) ? current : [...current, id]); const start = async () => { if (active.length !== size) return; setStarting(true); setError(''); try { const tournament = await api<{ id: string; status: string }>('/tournaments', { method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID() }, body: JSON.stringify({ size, candidateSource: 'AGENT_GENERATED', recordingIds: active.map(item => item.recordingId), explorationBrief: value.preferenceText || value.summary || '' }) }); if (tournament.status === 'DRAFT') await api(`/tournaments/${tournament.id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status: 'READY' }) }); setCreated(tournament.id); onTournamentCreated(tournament.id, size, cardMessageId) } catch { setError('赛事创建失败，请重试。') } finally { setStarting(false) } }; if (created || alreadyStarted) return <section className="tournament-launch-card"><p className="eyebrow">候选歌曲池</p><h2>赛事已创建</h2><p>首场对局已写入下方的赛事卡；你的候选池仍保留在这条对话中。</p></section>; const hasSnapshot = active.length > 0; return <section className="tournament-launch-card"><p className="eyebrow">候选歌曲池</p><h2>{value.status === 'ready_for_confirmation' ? `已为 ${size} 首赛事整理候选` : '候选池需要继续调整'}</h2><p>{value.summary || '候选池已保存；可在当前会话内继续确认与开赛。'}</p>{hasSnapshot ? <ol className="conversation-candidate-preview">{active.slice(0, 10).map(item => <li key={item.recordingId}>{item.coverUrl && <img src={item.coverUrl} alt="" />}<span><strong>{item.title}</strong><small>{item.artistName}</small></span><button onClick={() => remove(item.recordingId)} disabled={starting}>移除</button></li>)}{active.length > 10 && <li className="more">以及另外 {active.length - 10} 首参赛候选</li>}</ol> : <p className="conversation-card-count">这是较早版本保存的候选卡，缺少可展示的歌曲快照。重新构建后会把完整候选留在当前对话中。</p>}{hasSnapshot && <p className="conversation-card-count">当前参赛 {active.length} / {size} 首；移除后会由候补自动补位。</p>}{error && <p className="conversation-card-error">{error}</p>}<div>{hasSnapshot ? <button disabled={starting || active.length !== size} onClick={() => void start()}>{starting ? '正在创建赛事…' : '确认这组歌曲并开赛'}</button> : <button onClick={() => onRebuild(size, value.preferenceText || value.summary || '根据本轮已有的音乐偏好重新构建候选歌曲池')}>重新构建完整候选池</button>}</div></section> }
function ClarificationCard({ payload, agentRunId, onCompleted, onProgress, onPlan }: { payload?: string | null; agentRunId?: string | null; onCompleted: () => void; onProgress: (item: Progress) => void; onPlan: (item: Plan) => void }) {
  let value: { question?: string; reason?: string; options?: string[]; allowFreeText?: boolean; clarifications?: { mention?: string; candidates?: { name?: string; mbid?: string; country?: string; disambiguation?: string }[] }[]; preferenceText?: string } = {}
  try { value = JSON.parse(payload || '{}') } catch { /* degraded card */ }
  const [selections, setSelections] = useState<Record<string, { mention: string; mbid: string; name: string }>>({})
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [answer, setAnswer] = useState('')
  const mentions = value.clarifications?.map(item => item.mention).filter(Boolean) as string[] || []
  const artistClarification = mentions.length > 0
  const ready = artistClarification ? mentions.every(mention => selections[mention]) : Boolean(answer.trim())
  const choose = (mention: string, candidate: { name?: string; mbid?: string }) => { const name=candidate.name,mbid=candidate.mbid; if (!name || !mbid) return; setSelections(current => ({ ...current, [mention]: { mention, mbid, name } })) }
  const submit = async () => {
    if (!agentRunId || !ready) return
    setSubmitting(true); setError('')
    try {
      const response = await fetch(`/api/v1/agent-runs/${agentRunId}/answers`, { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID() }, body: JSON.stringify({ selections: Object.values(selections), answer: answer.trim() }) })
      if (!response.ok || !response.body) throw new Error(`请求失败（${response.status}）`)
      const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = ''; let completed = false
      const consume = (block: string) => { const type = block.match(/^event:\s*(.+)$/m)?.[1]?.trim(); const data = block.match(/^data:\s*(.+)$/m)?.[1]?.trim(); if (!type || !data) return; if (type === 'progress') onProgress(JSON.parse(data) as Progress); else if (type === 'plan_updated') onPlan(JSON.parse(data) as Plan); else if (type === 'message_completed') completed = true; else if (type === 'error') throw new Error('澄清答案暂时无法处理') }
      while (true) { const { value, done } = await reader.read(); buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done }); let boundary: number; while ((boundary = buffer.indexOf('\n\n')) >= 0) { consume(buffer.slice(0, boundary)); buffer = buffer.slice(boundary + 2) } if (done) break }
      if (!completed) throw new Error('澄清后的任务未返回最终结果')
      onCompleted()
    } catch { setError('补充信息后暂时无法继续本轮探索，请重试。') } finally { setSubmitting(false) }
  }
  return <section className="tournament-launch-card clarification-card"><p className="eyebrow">需要你补充一下</p><h2>{artistClarification ? '先确认你提到的是哪位艺人' : (value.question || '哪个方向更接近你的想法？')}</h2><p>{value.reason || (artistClarification ? '确认后会继续构建候选池，不需要重述偏好。' : '回答后会接着刚才的内容继续，不会丢失前面的对话。')}</p>{value.clarifications?.map((item, index) => <div key={`${item.mention}-${index}`}><strong>{item.mention}</strong><ul>{item.candidates?.map((candidate, itemIndex) => <li key={`${candidate.name}-${itemIndex}`}><button type="button" disabled={submitting} className={selections[item.mention || '']?.name === candidate.name ? 'selected' : ''} onClick={() => choose(item.mention || '', candidate)}>{candidate.name}{candidate.country ? ` · ${candidate.country}` : ''}{candidate.disambiguation ? ` · ${candidate.disambiguation}` : ''}</button></li>)}</ul></div>)}{!artistClarification && <div className="clarification-answer">{value.options?.length ? <div className="clarification-options">{value.options.map(option => <button type="button" key={option} className={answer === option ? 'selected' : ''} onClick={() => setAnswer(option)}>{option}</button>)}</div> : null}<textarea value={answer} onChange={event => setAnswer(event.target.value)} maxLength={2000} placeholder="补充你的想法" onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); if (ready) void submit() } }} /></div>}{error && <p className="conversation-card-error">{error}</p>}<div><button disabled={!ready || submitting || !agentRunId} onClick={() => void submit()}>{submitting ? '正在继续本轮探索…' : (artistClarification ? '确认艺人并继续' : '提交并继续')}</button></div></section>
}
function RecommendationNoticeCard({ payload }: { payload?: string | null }) { let value: { summary?: string; warnings?: { message?: string }[] } = {}; try { value = JSON.parse(payload || '{}') } catch { /* degraded card */ } return <section className="tournament-launch-card"><p className="eyebrow">探索需要补充</p><h2>这次还不足以组成一场比赛</h2><p>{value.summary || value.warnings?.[0]?.message || '请补充偏好的艺人、场景、语言或年代后继续。'}</p></section> }
function PublicMusicSourcesCard({ payload }: { payload?: string | null }) {
  let value: { title?: string; items?: { title?: string; url?: string; summary?: string; provider?: string }[] } = {}
  try { value = JSON.parse(payload || '{}') } catch { /* degraded card */ }
  return <section className="tournament-launch-card conversation-report-card"><p className="eyebrow">公开音乐资料</p><h2>{value.title || '沿着这些线索继续探索'}</h2><ol className="conversation-recommendations">{value.items?.map((item, index) => <li key={`${item.url}-${index}`}><strong>{item.title || '音乐资料'}</strong>{item.summary && <small>{item.summary}</small>}{item.url && <a href={item.url} target="_blank" rel="noreferrer">查看原始来源</a>}</li>)}</ol></section>
}
function MusicRecommendationsCard({ payload, conversationId }: { payload?: string | null; conversationId?: string }) {
  type Song = { recordingId?: string; title?: string; artistName?: string; albumTitle?: string; coverUrl?: string; reason?: string; sourceUrl?: string; searchUrl?: string; verificationStatus?: string }
  type Artist = { artistId?: string; artistName?: string; reason?: string; sourceUrl?: string; searchUrl?: string; verificationStatus?: string }
  let value: { title?: string; summary?: string; songs?: Song[]; artists?: Artist[]; sources?: { title?: string; url?: string }[]; contextMode?: string; basedOnCardId?: string; appliedInstruction?: string } = {}
  try { value = JSON.parse(payload || '{}') } catch { /* degraded card */ }
  const [feedback, setFeedback] = useState<Record<string, string>>({})
  const listening = useListeningPreview()
  const seenSongs = new Set<string>()
  const displaySongs = (value.songs || []).filter(item => { const key = `${item.recordingId || ''}|${item.artistName || ''}|${item.title || ''}`.toLocaleLowerCase().replace(/[^\p{L}\p{N}|]/gu, ''); if (seenSongs.has(key)) return false; seenSongs.add(key); return true })
  const seenArtists = new Set<string>()
  const displayArtists = (value.artists || []).filter(item => { const key = (item.artistId || item.artistName || '').toLocaleLowerCase().replace(/[^\p{L}\p{N}]/gu, ''); if (seenArtists.has(key)) return false; seenArtists.add(key); return true })
  const sendFeedback = async (kind: 'SONG' | 'ARTIST', key: string, targetRef: object, preference: 'LIKE' | 'DISLIKE') => {
    if (!conversationId || feedback[key]) return
    try {
      await api('/recommendation-feedback', { method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID() }, body: JSON.stringify({ conversationId, targetType: kind, targetRef, feedback: preference }) })
      setFeedback(current => ({ ...current, [key]: preference }))
    } catch { /* recommendation feedback is best-effort */ }
  }
  return <section className="tournament-launch-card conversation-report-card music-recommendation-card">
    <p className="eyebrow">{value.contextMode === 'REFINED' ? '音乐推荐 · 基于上一轮调整' : '音乐推荐'}</p>
    <h2>{value.title || '这轮值得继续听的方向'}</h2>
    {value.appliedInstruction && <small className="recommendation-refinement">已应用你的调整：{value.appliedInstruction}</small>}
    {value.summary && <p>{value.summary}</p>}
    {displaySongs.length ? <><h3>歌曲</h3><ol className="music-recommendation-list">{displaySongs.map((item, index) => {
      const feedbackKey = `song-${item.recordingId || index}`
      const playback = item.recordingId ? listening.stateFor(item.recordingId) : null
      const content = <><MusicCover url={item.coverUrl} title={item.title} /><span><strong>{item.title}</strong><small>{item.artistName}{item.albumTitle ? ` · ${item.albumTitle}` : ''}</small><em>{item.reason}</em>{playback && <span className={`recommendation-playback ${playback.phase}`}>{playbackLabel(playback.phase)}</span>}</span></>
      return <li key={`${feedbackKey}-${index}`}>{item.recordingId ? <button className="music-recommendation-main" type="button" onMouseEnter={() => void listening.prefetch(item.recordingId!)} onFocus={() => void listening.prefetch(item.recordingId!)} onClick={() => void listening.toggle(item.recordingId!)}>{content}</button> : <div className="music-recommendation-main">{content}</div>}<div className="music-recommendation-actions"><small>{item.verificationStatus === 'MUSICBRAINZ_VERIFIED' ? 'MusicBrainz 已核验' : '公开资料发现'}</small>{item.searchUrl && <a href={item.searchUrl} target="_blank" rel="noreferrer">去平台</a>}<button onClick={() => void sendFeedback('SONG', feedbackKey, { recordingId: item.recordingId, title: item.title, artistName: item.artistName }, 'LIKE')}>喜欢</button><button onClick={() => void sendFeedback('SONG', feedbackKey, { recordingId: item.recordingId, title: item.title, artistName: item.artistName }, 'DISLIKE')}>不适合</button>{feedback[feedbackKey] && <small>已记录</small>}</div></li>
    })}</ol></> : null}
    {displayArtists.length ? <><h3>艺人</h3><ol className="music-artist-list">{displayArtists.map((item, index) => { const key=`artist-${item.artistId || item.artistName || index}`; return <li key={key}><a href={item.searchUrl || item.sourceUrl} target="_blank" rel="noreferrer"><strong>{item.artistName}</strong><small>{item.reason}</small></a><div className="music-recommendation-actions"><small>{item.verificationStatus === 'MUSICBRAINZ_VERIFIED' ? 'MusicBrainz 已核验' : '公开资料发现'}</small><button onClick={() => void sendFeedback('ARTIST', key, { artistId: item.artistId, artistName: item.artistName }, 'LIKE')}>喜欢</button><button onClick={() => void sendFeedback('ARTIST', key, { artistId: item.artistId, artistName: item.artistName }, 'DISLIKE')}>不适合</button>{feedback[key] && <small>已记录</small>}</div></li> })}</ol></> : null}
    {value.sources?.length ? <details className="music-recommendation-sources"><summary>查看推荐依据</summary>{value.sources.map((item, index) => <a key={`${item.url}-${index}`} href={item.url} target="_blank" rel="noreferrer">{item.title || '公开资料'}</a>)}</details> : null}
  </section>
}
function ExplorationReportCard({ payload, conversationId }: { payload?: string | null; conversationId?: string }) {
  let value: { summary?: string; dimensions?: { label?: string; summary?: string }[]; directions?: string[]; personalityEasterEgg?: string; disclaimer?: string; claims?: { text?: string; confidence?: string; signalRefs?: string[]; boundary?: string }[] } = {}
  try { value = JSON.parse(payload || '{}') } catch { /* degraded card */ }
  const [feedbackSent, setFeedbackSent] = useState('')
  const sendFeedback = async (feedback: 'LIKE' | 'NEUTRAL' | 'DISLIKE') => {
    if (!conversationId) return
    try { await api('/recommendation-feedback', { method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID() }, body: JSON.stringify({ conversationId, targetType: 'EXPLORATION_REPORT', targetRef: { summary: value.summary || '' }, feedback }) }); setFeedbackSent(feedback) } catch { /* feedback is best-effort */ }
  }
  return <section className="tournament-launch-card conversation-report-card"><p className="eyebrow">音乐探索报告</p><h2>这段对话留下的偏好线索</h2><p>{value.summary || '报告已生成；继续补充喜欢或跳过的作品，可以让下一份结论更具体。'}</p>{value.dimensions?.map((item, index) => <p key={`${item.label}-${index}`}><strong>{item.label}</strong> · {item.summary}</p>)}{value.claims?.slice(0, 3).map((item, index) => <small key={index}>依据：{(item.signalRefs || []).join('、') || 'conversation'}{item.boundary ? ` · ${item.boundary}` : ''}</small>)}{value.directions?.length ? <ol className="conversation-recommendations">{value.directions.map((item, index) => <li key={index}>{item}</li>)}</ol> : null}{value.personalityEasterEgg && <p className="conversation-report-easter-egg">{value.personalityEasterEgg}</p>}{value.disclaimer && <small>{value.disclaimer}</small>}<div>{!feedbackSent ? <><button onClick={() => void sendFeedback('LIKE')}>有帮助</button><button onClick={() => void sendFeedback('NEUTRAL')}>一般</button><button onClick={() => void sendFeedback('DISLIKE')}>不太准</button></> : <small>已记录你的反馈：{feedbackSent}</small>}</div></section>
}
function TournamentStatusCard({ payload, onReportReady, onProgress, onPlan, onRunActive }: { payload?: string | null; onReportReady: (report: Report, tournamentId: string, championTitle: string) => void; onProgress: (item: Progress) => void; onPlan: (item: Plan) => void; onRunActive: (active: boolean) => void }) { let value: { tournamentId?: string; size?: number } = {}; try { value = JSON.parse(payload || '{}') } catch { /* display degraded card */ } const [tournament, setTournament] = useState<Tournament | null>(null); const [loading, setLoading] = useState(false); const [reporting, setReporting] = useState(false); const [error, setError] = useState(''); const refresh = async () => { if (!value.tournamentId) return; try { setTournament(await api<Tournament>(`/tournaments/${value.tournamentId}`)) } catch { setError('赛事状态暂时无法读取。') } }; useEffect(() => { void refresh() }, [value.tournamentId]); const current = tournament?.currentMatch; const entries = new Map(tournament?.entries.map(item => [item.id, item]) || []); const left = current ? entries.get(current.leftEntryId) : undefined; const right = current ? entries.get(current.rightEntryId) : undefined; const vote = async (entryId: string) => { if (!current) return; setLoading(true); setError(''); try { await api(`/tournament-matches/${current.id}/votes`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID() }, body: JSON.stringify({ selectedEntryId: entryId }) }); await refresh() } catch { setError('这次选择没有保存成功，请重试。') } finally { setLoading(false) } }; const generateReport = async () => { if (!value.tournamentId || !tournament) return; setReporting(true); onRunActive(true); setError(''); try { const report = await createAndWaitForReport(value.tournamentId, onProgress, onPlan); const champion = tournament.entries[0]?.title || ''; onReportReady(report, value.tournamentId, champion) } catch (error) { setError(friendlyError(error)) } finally { setReporting(false); onRunActive(false) } }; return <section className="tournament-launch-card conversation-match-card"><p className="eyebrow">歌曲世界杯 · {tournament?.status === 'COMPLETED' ? '已结束' : '进行中'}</p><h2>{tournament?.status === 'COMPLETED' ? '这场比赛已结束' : `${tournament?.size || value.size || 16} 首歌曲世界杯`}</h2>{tournament && <p>已完成 {tournament.completedVoteCount} / {tournament.size - 1} 场选择。</p>}{current && left && right ? <div className="conversation-match-choice"><button disabled={loading} onClick={() => void vote(left.id)}>{left.coverUrl && <img src={left.coverUrl} alt="" />}<strong>{left.title}</strong><small>{left.artistName}</small></button><span>VS</span><button disabled={loading} onClick={() => void vote(right.id)}>{right.coverUrl && <img src={right.coverUrl} alt="" />}<strong>{right.title}</strong><small>{right.artistName}</small></button></div> : tournament?.status === 'COMPLETED' ? <p>冠军已经产生。现在可以把本场选择沉淀为偏好报告。</p> : <p>正在准备下一场对局…</p>}{error && <p className="conversation-card-error">{error}</p>}<div><button onClick={() => void refresh()}>{loading ? '正在保存…' : '刷新赛事状态'}</button>{tournament?.status === 'COMPLETED' && <button onClick={() => void generateReport()} disabled={reporting}>{reporting ? '正在生成报告…' : '生成赛后报告'}</button>}</div></section> }
function ReportCard({ payload }: { payload?: string | null }) { let value: { tournamentId?: string; status?: string; championTitle?: string } = {}; try { value = JSON.parse(payload || '{}') } catch { /* display degraded card */ } const [report, setReport] = useState<Report | null>(null); const [expanded, setExpanded] = useState(false); useEffect(() => { if (value.tournamentId) void api<Report>(`/tournaments/${value.tournamentId}/preference-report`).then(setReport).catch(() => undefined) }, [value.tournamentId]); const ready = report?.status === 'READY' || value.status === 'READY'; const body = report?.report; return <section className="tournament-launch-card conversation-report-card"><p className="eyebrow">赛后偏好报告</p><h2>{value.championTitle ? `冠军《${value.championTitle}》的探索报告` : '本场音乐探索报告'}</h2><p>{ready ? (body?.summary || '报告已完成。你可以继续追问本场的选择轨迹与推荐方向。') : '报告正在生成中。'}</p>{ready && body?.songRecommendations?.length ? <ol className="conversation-recommendations">{body.songRecommendations.slice(0, expanded ? 7 : 3).map((item, index) => <li key={`${item.title}-${index}`}><strong>{item.title}</strong><small>{item.artistName} · {item.reason}</small>{item.searchUrl && <a href={item.searchUrl} target="_blank" rel="noreferrer">去平台搜索</a>}</li>)}</ol> : null}{expanded && body?.artistRecommendations?.length ? <ol className="conversation-recommendations">{body.artistRecommendations.map((item, index) => <li key={`${item.artistName}-${index}`}><strong>{item.artistName}</strong><small>{item.reason}</small>{item.searchUrl && <a href={item.searchUrl} target="_blank" rel="noreferrer">去平台搜索</a>}</li>)}</ol> : null}{expanded && body?.personalityEasterEgg && <p className="conversation-report-easter-egg">{body.personalityEasterEgg}</p>}<div>{ready && <button onClick={() => setExpanded(value => !value)}>{expanded ? '收起报告详情' : '展开完整报告'}</button>}</div></section> }
