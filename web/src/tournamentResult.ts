export type ResultEntry = { id: string; recordingId: string; title: string; artistName: string; albumTitle?: string; coverUrl?: string; listeningSearchUrl: string }
export type ResultMatch = { id: string; roundNumber: number; matchIndex: number; leftEntryId: string | null; rightEntryId: string | null; winnerEntryId: string | null; status: string }
export type ResultTournament = { id: string; size: number; completedAt?: string | null; entries: ResultEntry[]; matches: ResultMatch[] }

export type TournamentResult = {
  champion?: ResultEntry
  runnerUp?: ResultEntry
  fourFinalists: ResultEntry[]
  finalMatch?: ResultMatch
  maxRound: number
  championMatchIds: Set<string>
  rounds: ResultMatch[][]
}

export function deriveTournamentResult(tournament: ResultTournament): TournamentResult {
  const matches = [...tournament.matches].sort((a, b) => a.roundNumber - b.roundNumber || a.matchIndex - b.matchIndex)
  const maxRound = Math.max(0, ...matches.map(match => match.roundNumber))
  const byId = new Map(tournament.entries.map(entry => [entry.id, entry]))
  const finalMatch = matches.find(match => match.roundNumber === maxRound)
  const champion = finalMatch?.winnerEntryId ? byId.get(finalMatch.winnerEntryId) : undefined
  const runnerUpId = finalMatch && finalMatch.winnerEntryId === finalMatch.leftEntryId ? finalMatch.rightEntryId : finalMatch?.leftEntryId
  const runnerUp = runnerUpId ? byId.get(runnerUpId) : undefined
  const semifinalists = matches.filter(match => match.roundNumber === maxRound - 1)
    .map(match => match.winnerEntryId === match.leftEntryId ? match.rightEntryId : match.leftEntryId)
    .filter((id): id is string => Boolean(id)).map(id => byId.get(id)).filter((entry): entry is ResultEntry => Boolean(entry))
  return {
    champion,
    runnerUp,
    fourFinalists: semifinalists,
    finalMatch,
    maxRound,
    championMatchIds: new Set(champion ? matches.filter(match => match.winnerEntryId === champion.id).map(match => match.id) : []),
    rounds: Array.from({ length: maxRound }, (_, index) => matches.filter(match => match.roundNumber === index + 1)),
  }
}

export function roundLabel(round: number, maxRound: number) {
  if (round === maxRound) return '决赛'
  if (round === maxRound - 1) return '半决赛'
  if (round === maxRound - 2) return '四分之一决赛'
  return `第 ${round} 轮`
}

export function completedDate(value?: string | null) {
  if (!value) return '刚刚完成'
  return new Intl.DateTimeFormat('zh-CN', { month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(new Date(value))
}
