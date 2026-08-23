package com.indiesoundquest.internal;

import com.indiesoundquest.tournament.domain.*; import com.indiesoundquest.tournament.repository.*;
import java.util.*;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;

@RestController @RequestMapping("/internal/v1")
public class MusicCatalogInternalController {
  private final RecordingRepository recordings; private final TournamentRepository tournaments; private final TournamentEntryRepository entries; private final TournamentMatchRepository matches; private final MusicBrainzCatalogService musicBrainz; private final String token;
  public MusicCatalogInternalController(RecordingRepository recordings,TournamentRepository tournaments,TournamentEntryRepository entries,TournamentMatchRepository matches,MusicBrainzCatalogService musicBrainz,@Value("${agent.internal.service-token:change-me}") String token){this.recordings=recordings;this.tournaments=tournaments;this.entries=entries;this.matches=matches;this.musicBrainz=musicBrainz;this.token=token;}
  @PostMapping("/music-catalog/search") Map<String,Object> search(@RequestHeader("Authorization") String authorization,@RequestBody SearchRequest request){
    if(!authorization.equals("Bearer "+token)) throw new org.springframework.web.server.ResponseStatusException(HttpStatus.UNAUTHORIZED);
    var source=request.artistIds()==null||request.artistIds().isEmpty()?recordings.findAllWithArtistOrderByArtistAndSeedRank():recordings.findByArtistIdInOrderBySeedRankAsc(request.artistIds());
    var items=source.stream().limit(Math.min(request.limit()==null?80:request.limit(),80)).map(r->Map.<String,Object>of("id",r.getId(),"title",r.getTitle(),"artistId",r.getArtist().getId(),"artistName",r.getArtist().getName(),"albumTitle",Optional.ofNullable(r.getAlbumTitle()).orElse(""),"coverStatus",Optional.ofNullable(r.getCoverStatus()).orElse("UNAVAILABLE"),"catalogSource",Optional.ofNullable(r.getCatalogSource()).orElse("LOCAL_SEED"))).toList(); return Map.of("items",items);
  }
  record SearchRequest(String query,List<UUID> artistIds,Integer limit){}
  @PostMapping("/music-catalog/musicbrainz/resolve-and-import") Map<String,Object> resolveAndImport(@RequestHeader("Authorization") String authorization,@RequestBody ResolveRequest request){
    authorize(authorization);
    var hints=Optional.ofNullable(request.hints()).orElseGet(List::of).stream().limit(20).map(hint->new MusicBrainzCatalogService.Hint(hint.title(),hint.artistName(),hint.sourceUrl())).toList();
    return Map.of("items",musicBrainz.resolveAndImport(hints));
  }
  record ResolveRequest(List<RecordingHint> hints){}
  record RecordingHint(String title,String artistName,String sourceUrl){}
  @PostMapping("/music-catalog/musicbrainz/resolve-artists") Map<String,Object> resolveArtists(@RequestHeader("Authorization") String authorization,@RequestBody ArtistResolveRequest request){
    authorize(authorization);
    return Map.of("items", musicBrainz.resolveArtistCandidates(Optional.ofNullable(request.names()).orElseGet(List::of)));
  }
  record ArtistResolveRequest(List<String> names){}
  @GetMapping("/tournaments/{tournamentId}/report-facts") @org.springframework.transaction.annotation.Transactional(readOnly=true) Map<String,Object> reportFacts(@RequestHeader("Authorization") String authorization,@RequestHeader("X-Guest-Session-Id") UUID guestSessionId,@PathVariable UUID tournamentId){
    if(!authorization.equals("Bearer "+token)) throw new org.springframework.web.server.ResponseStatusException(HttpStatus.UNAUTHORIZED); var tournament=tournaments.findByIdAndGuestSessionIdAndDeletedAtIsNull(tournamentId,guestSessionId).orElseThrow(()->new org.springframework.web.server.ResponseStatusException(HttpStatus.NOT_FOUND)); if(tournament.getStatus()!=TournamentStatus.COMPLETED) throw new org.springframework.web.server.ResponseStatusException(HttpStatus.CONFLICT,"tournament is not completed");
    var entryById=new HashMap<UUID,TournamentEntry>(); entries.findByTournamentId(tournamentId).forEach(entry->entryById.put(entry.getId(),entry));
    var entryFacts=entryById.values().stream().map(entry->Map.<String,Object>of("entryId",entry.getId(),"recordingId",entry.getRecording().getId(),"artistId",entry.getRecording().getArtist().getId(),"title",entry.getTitleSnapshot(),"artistName",entry.getArtistNameSnapshot(),"albumTitle",Optional.ofNullable(entry.getAlbumTitleSnapshot()).orElse(""))).toList();
    var matchFacts=matches.findByTournamentIdOrderByRoundNumberAscMatchIndexAsc(tournamentId).stream().filter(match->match.getStatus()==MatchStatus.COMPLETED).map(match->Map.<String,Object>of("matchId",match.getId(),"roundNumber",match.getRoundNumber(),"matchIndex",match.getMatchIndex(),"leftEntryId",match.getLeftEntryId(),"rightEntryId",match.getRightEntryId(),"winnerEntryId",match.getWinnerEntryId())).toList();
    return Map.of("tournamentId",tournamentId,"size",tournament.getSize(),"completedVoteCount",matchFacts.size(),"entries",entryFacts,"matches",matchFacts);
  }
  private void authorize(String authorization){if(!Objects.equals(authorization,"Bearer "+token)) throw new org.springframework.web.server.ResponseStatusException(HttpStatus.UNAUTHORIZED);}
}
