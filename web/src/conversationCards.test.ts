import { describe, expect, it } from 'vitest'
import { candidateTournamentCardFor, legacyCandidateHasFollowingTournament } from './ConversationApp'

const base = { role: 'ASSISTANT' as const, content: null, cardType: null, cardPayloadJson: null, status: 'COMPLETED' as const, createdAt: '2026-08-30T00:00:00Z' }

describe('conversation tournament cards', () => {
  it('recognises an older candidate card as started when its following card is the tournament', () => {
    const timeline = [
      { ...base, id: 'candidate', type: 'CANDIDATE_POOL_CARD' as const, sequenceNumber: 3 },
      { ...base, id: 'tournament', type: 'TOURNAMENT_CARD' as const, sequenceNumber: 4 },
    ]
    expect(legacyCandidateHasFollowingTournament(timeline, 0)).toBe(true)
  })

  it('does not let a later tournament suppress a newer candidate pool', () => {
    const timeline = [
      { ...base, id: 'first-candidate', type: 'CANDIDATE_POOL_CARD' as const, sequenceNumber: 3 },
      { ...base, id: 'first-tournament', type: 'TOURNAMENT_CARD' as const, sequenceNumber: 4 },
      { ...base, id: 'new-candidate', type: 'CANDIDATE_POOL_CARD' as const, sequenceNumber: 5 },
    ]
    expect(legacyCandidateHasFollowingTournament(timeline, 2)).toBe(false)
  })

  it('prefers the explicit source card link for newly persisted tournaments', () => {
    const matchesCandidate = candidateTournamentCardFor('candidate')
    expect(matchesCandidate({ ...base, id: 'tournament', type: 'TOURNAMENT_CARD', sequenceNumber: 4, cardPayloadJson: JSON.stringify({ sourceCandidateMessageId: 'candidate' }) })).toBe(true)
  })
})
