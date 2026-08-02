package com.indiesoundquest.tournament.web;
import com.indiesoundquest.tournament.domain.*; import java.util.*;
public record TournamentDetailResponse(UUID id, TournamentStatus status, int size, int completedVoteCount, List<Entry> entries, List<Match> matches, Match currentMatch) {
 public record Entry(UUID id,String title,String artistName,String albumTitle,String versionLabel,String coverUrl,String coverStatus) { static Entry from(TournamentEntry e){return new Entry(e.getId(),e.getTitleSnapshot(),e.getArtistNameSnapshot(),e.getAlbumTitleSnapshot(),e.getVersionLabelSnapshot(),e.getRecording().getCoverUrl(),e.getRecording().getCoverStatus());} }
 public record Match(UUID id,int roundNumber,int matchIndex,UUID leftEntryId,UUID rightEntryId,UUID winnerEntryId,MatchStatus status) { static Match from(TournamentMatch m){return new Match(m.getId(),m.getRoundNumber(),m.getMatchIndex(),m.getLeftEntryId(),m.getRightEntryId(),m.getWinnerEntryId(),m.getStatus());} }
}
