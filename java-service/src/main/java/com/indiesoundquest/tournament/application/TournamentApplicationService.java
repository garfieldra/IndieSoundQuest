package com.indiesoundquest.tournament.application;

import com.indiesoundquest.tournament.domain.*;
import com.indiesoundquest.tournament.repository.*;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.security.SecureRandom;
import java.util.*;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class TournamentApplicationService {
  private final TournamentRepository tournaments; private final TournamentEntryRepository entries; private final GuestSessionRepository guestSessions;
  private final TournamentMatchRepository matches; private final VoteRepository votes; private final MusicPreferenceProfileService profiles; private final SingleEliminationBracketGenerator brackets = new SingleEliminationBracketGenerator();
  private final SecureRandom random = new SecureRandom();
  public TournamentApplicationService(TournamentRepository tournaments, TournamentEntryRepository entries, TournamentMatchRepository matches, VoteRepository votes, MusicPreferenceProfileService profiles, GuestSessionRepository guestSessions) { this.tournaments=tournaments; this.entries=entries; this.matches=matches; this.votes=votes; this.profiles=profiles; this.guestSessions=guestSessions; }

  public record CreationResult(Tournament tournament, boolean replayed) {}

  @Transactional
  public CreationResult createDraft(GuestSession guest, Artist artist, List<Recording> recordings, int size, String idempotencyKey) {
    if (recordings.size()!=size) throw new IllegalArgumentException("recording count must match tournament size");
    if (recordings.stream().map(Recording::getId).distinct().count()!=size) throw new IllegalArgumentException("recordings must be unique");
    var lockedGuest=guestSessions.findByIdForUpdate(guest.getId()).orElseThrow(NoSuchElementException::new);
    var requestHash=creationRequestHash(CandidateSource.POPULAR,size,artist.getId(),recordings,null);
    var replay=replay(lockedGuest.getId(),idempotencyKey,requestHash);
    if(replay.isPresent()) return new CreationResult(replay.get(),true);
    Tournament tournament=tournaments.save(Tournament.draft(UUID.randomUUID(),lockedGuest,artist,size,random.nextLong(),idempotencyKey,requestHash));
    entries.saveAll(recordings.stream().map(recording -> TournamentEntry.from(UUID.randomUUID(),tournament,recording,artist.getName())).toList());
    return new CreationResult(tournament,false);
  }
  @Transactional
  public CreationResult createAgentDraft(GuestSession guest, List<Recording> recordings, int size, String explorationBrief, String idempotencyKey) {
    if (recordings.size()!=size || recordings.stream().map(Recording::getId).distinct().count()!=size) throw new IllegalArgumentException("agent recordings are invalid");
    var lockedGuest=guestSessions.findByIdForUpdate(guest.getId()).orElseThrow(NoSuchElementException::new);
    var requestHash=creationRequestHash(CandidateSource.AGENT_GENERATED,size,null,recordings,explorationBrief);
    var replay=replay(lockedGuest.getId(),idempotencyKey,requestHash);
    if(replay.isPresent()) return new CreationResult(replay.get(),true);
    Tournament tournament=tournaments.save(Tournament.agentDraft(UUID.randomUUID(),lockedGuest,size,random.nextLong(),explorationBrief,idempotencyKey,requestHash));
    entries.saveAll(recordings.stream().map(recording -> TournamentEntry.from(UUID.randomUUID(),tournament,recording,recording.getArtist().getName())).toList());
    return new CreationResult(tournament,false);
  }

  private Optional<Tournament> replay(UUID guestSessionId,String idempotencyKey,String requestHash) {
    var existing=tournaments.findByGuestSessionIdAndCreationIdempotencyKey(guestSessionId,idempotencyKey);
    if(existing.isPresent()&&!requestHash.equals(existing.get().getCreationRequestHash())) throw new IdempotencyKeyConflictException();
    return existing;
  }

  private String creationRequestHash(CandidateSource source,int size,UUID artistId,List<Recording> recordings,String explorationBrief) {
    var canonical=new StringBuilder(source.name()).append('|').append(size).append('|').append(artistId==null?"":artistId).append('|');
    recordings.forEach(recording->canonical.append(recording.getId()).append(','));
    canonical.append('|').append(explorationBrief==null?"":explorationBrief);
    try {
      var bytes=MessageDigest.getInstance("SHA-256").digest(canonical.toString().getBytes(StandardCharsets.UTF_8));
      return HexFormat.of().formatHex(bytes);
    } catch (NoSuchAlgorithmException exception) {
      throw new IllegalStateException("SHA-256 unavailable",exception);
    }
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
    if (match.getNextMatchId()==null) { tournament.complete(selectedEntryId); profiles.refresh(tournament.getGuestSession()); }
    else { var next=matches.findById(match.getNextMatchId()).orElseThrow(); next.placeWinner(selectedEntryId,match.getNextSlot()); }
    return tournament;
  }
}
