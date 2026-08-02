package com.indiesoundquest.tournament.application;

import com.indiesoundquest.tournament.domain.*;
import com.indiesoundquest.tournament.repository.*;
import java.security.SecureRandom;
import java.util.*;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class TournamentApplicationService {
  private final TournamentRepository tournaments; private final TournamentEntryRepository entries;
  private final TournamentMatchRepository matches; private final VoteRepository votes; private final SingleEliminationBracketGenerator brackets = new SingleEliminationBracketGenerator();
  private final SecureRandom random = new SecureRandom();
  public TournamentApplicationService(TournamentRepository tournaments, TournamentEntryRepository entries, TournamentMatchRepository matches, VoteRepository votes) { this.tournaments=tournaments; this.entries=entries; this.matches=matches; this.votes=votes; }

  @Transactional
  public Tournament createDraft(GuestSession guest, Artist artist, List<Recording> recordings, int size) {
    if (recordings.size()!=size) throw new IllegalArgumentException("recording count must match tournament size");
    if (recordings.stream().map(Recording::getId).distinct().count()!=size) throw new IllegalArgumentException("recordings must be unique");
    Tournament tournament=tournaments.save(Tournament.draft(UUID.randomUUID(),guest,artist,size,random.nextLong()));
    entries.saveAll(recordings.stream().map(recording -> TournamentEntry.from(UUID.randomUUID(),tournament,recording,artist.getName())).toList());
    return tournament;
  }

  @Transactional
  public Tournament prepare(UUID tournamentId, UUID guestSessionId) {
    Tournament tournament=tournaments.findByIdAndGuestSessionIdAndDeletedAtIsNull(tournamentId,guestSessionId).orElseThrow(NoSuchElementException::new);
    var tournamentEntries=entries.findByTournamentId(tournamentId);
    if (tournamentEntries.size()!=tournament.getSize()) throw new IllegalStateException("entry count is invalid");
    tournament.prepare();
    var plan=brackets.generate(tournamentEntries.stream().map(TournamentEntry::getId).toList(), tournament.getBracketSeed());
    matches.saveAll(plan.matches().stream().map(match -> TournamentMatch.planned(match.id(),tournament,match.roundNumber(),match.matchIndex(),match.leftEntryId(),match.rightEntryId(),match.nextMatchId(),match.nextSlot())).toList());
    return tournament;
  }

  @Transactional
  public Tournament vote(UUID matchId, UUID selectedEntryId, UUID guestSessionId, String idempotencyKey) {
    var replay=votes.findByIdempotencyKey(idempotencyKey);
    if (replay.isPresent()) {
      var replayedMatch=matches.findById(replay.get().getMatchId()).orElseThrow();
      return tournaments.findById(replayedMatch.getTournament().getId()).orElseThrow();
    }
    var match=matches.findById(matchId).orElseThrow(NoSuchElementException::new);
    var tournament=match.getTournament();
    if (!tournament.getGuestSession().getId().equals(guestSessionId)) throw new SecurityException("tournament access denied");
    match.complete(selectedEntryId);
    votes.save(Vote.create(matchId,selectedEntryId,idempotencyKey));
    tournament.startIfNeeded();
    if (match.getNextMatchId()==null) tournament.complete(selectedEntryId);
    else { var next=matches.findById(match.getNextMatchId()).orElseThrow(); next.placeWinner(selectedEntryId,match.getNextSlot()); }
    return tournament;
  }
}
