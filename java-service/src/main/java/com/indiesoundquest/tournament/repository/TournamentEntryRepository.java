package com.indiesoundquest.tournament.repository;
import com.indiesoundquest.tournament.domain.TournamentEntry;
import java.util.*;
import org.springframework.data.jpa.repository.JpaRepository;
public interface TournamentEntryRepository extends JpaRepository<TournamentEntry, UUID> { List<TournamentEntry> findByTournamentId(UUID tournamentId); }
