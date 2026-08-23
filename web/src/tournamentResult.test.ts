import { describe, expect, it } from 'vitest'
import { deriveTournamentResult, type ResultMatch, type ResultTournament } from './tournamentResult'

function tournament(size: 16 | 32): ResultTournament {
  const entries = Array.from({ length: size }, (_, index) => ({ id: `e${index}`, recordingId: `r${index}`, title: `Song ${index}`, artistName: `Artist ${index}`, listeningSearchUrl: '#' }))
  let current = entries.map(entry => entry.id); const matches: ResultMatch[] = []; let round = 1
  while (current.length > 1) {
    const next: string[] = []
    for (let index = 0; index < current.length; index += 2) { const winner = current[index]; matches.push({ id: `m${round}-${index / 2}`, roundNumber: round, matchIndex: index / 2, leftEntryId: current[index], rightEntryId: current[index + 1], winnerEntryId: winner, status: 'COMPLETED' }); next.push(winner) }
    current = next; round += 1
  }
  return { id: 't1', size, entries, matches }
}

describe('deriveTournamentResult', () => {
  it.each([16, 32] as const)('derives champion, runner-up and four finalists for %i entries', size => {
    const result = deriveTournamentResult(tournament(size))
    expect(result.champion?.id).toBe('e0')
    expect(result.runnerUp?.id).toBe(`e${size / 2}`)
    expect(result.fourFinalists.map(entry => entry.id)).toEqual([`e${size / 4}`, `e${size * 3 / 4}`])
    expect(result.rounds).toHaveLength(Math.log2(size))
    expect(result.championMatchIds.size).toBe(Math.log2(size))
  })
})
