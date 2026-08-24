export type CandidateWarning = { code: string; message: string }

export type CandidateItem = {
  recordingId: string
  title: string
  artistName: string
  albumTitle: string
  coverUrl: string
  coverStatus: string
  catalogSource?: 'LOCAL_SEED' | 'EXTERNAL_VERIFIED'
  listeningSearchUrl: string
  reason: string
  explorationRationale?: { kind: string; text: string }[]
  evidenceSummary?: { title?: string; domain?: string; url: string; trustLevel?: string }[]
  discoverySources?: { type?: string; provider?: string; url?: string; query?: string }[]
  qualityDimensions?: Record<string, string>
  poolRole?: 'MAIN' | 'RESERVE'
  verificationStatus?: 'VERIFIED' | 'CATALOG_VERIFIED'
}

export type CandidatePool = {
  requestId: string
  size: 16 | 32
  reserveSize: number
  candidateSummary: string
  items: CandidateItem[]
  recordingIds: string[]
  warnings: CandidateWarning[]
  intentMode?: 'ARTIST_LOCKED' | 'ARTIST_SEEDED' | 'OPEN_DISCOVERY'
  terminationReason?: string
}

export const intentModeLabel: Record<NonNullable<CandidatePool['intentMode']>, string> = {
  ARTIST_LOCKED: '仅限指定艺人',
  ARTIST_SEEDED: '从指定艺人向外探索',
  OPEN_DISCOVERY: '按文字偏好开放探索',
}

export type CandidatePoolResponse = {
  status: 'needs_clarification' | 'ready_for_confirmation' | 'insufficient_candidates'
  candidatePool: CandidatePool | null
  clarifications?: ArtistClarification[]
}

export type ArtistChoice = { mbid: string; name: string; country?: string; type?: string; disambiguation?: string; begin?: string; end?: string }
export type ArtistClarification = { mention: string; candidates: ArtistChoice[]; reason?: string }

export function deriveCandidateSelection(
  orderedItems: CandidateItem[],
  excludedIds: ReadonlySet<string>,
  size: 16 | 32,
) {
  const eligibleItems = orderedItems.filter(item => !excludedIds.has(item.recordingId))
  return {
    eligibleItems,
    activeItems: eligibleItems.slice(0, size),
    reserveItems: eligibleItems.slice(size),
    removedItems: orderedItems.filter(item => excludedIds.has(item.recordingId)),
    canRemove: eligibleItems.length > size,
  }
}
