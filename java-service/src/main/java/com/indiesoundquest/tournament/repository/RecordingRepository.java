package com.indiesoundquest.tournament.repository;

import com.indiesoundquest.tournament.domain.Recording;
import java.util.*;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;

public interface RecordingRepository extends JpaRepository<Recording, UUID> {
  List<Recording> findByArtistIdOrderBySeedRankAsc(UUID artistId);
  Optional<Recording> findByMusicbrainzMbid(String musicbrainzMbid);
  Optional<Recording> findFirstByArtistIdAndTitleIgnoreCase(UUID artistId, String title);
  @Query("select coalesce(max(r.seedRank), 0) from Recording r") int findMaxSeedRank();
  @Query("select r from Recording r join fetch r.artist where r.artist.id in :artistIds order by r.seedRank") List<Recording> findByArtistIdInOrderBySeedRankAsc(List<UUID> artistIds);
  @Query("select r from Recording r join fetch r.artist order by r.artist.sortName, r.seedRank") List<Recording> findAllWithArtistOrderByArtistAndSeedRank();
  @Query("select r from Recording r join fetch r.artist where r.id in :ids") List<Recording> findByIdInWithArtist(List<UUID> ids);
  @Query("select r from Recording r join fetch r.artist where r.id = :id") Optional<Recording> findByIdWithArtist(UUID id);
}
