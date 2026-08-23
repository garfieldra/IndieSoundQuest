package com.indiesoundquest.tournament.repository;

import com.indiesoundquest.tournament.domain.GuestSession;
import jakarta.persistence.LockModeType;
import java.util.*;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

public interface GuestSessionRepository extends JpaRepository<GuestSession, UUID> {
  Optional<GuestSession> findByTokenHash(String tokenHash);

  @Lock(LockModeType.PESSIMISTIC_WRITE)
  @Query("select guest from GuestSession guest where guest.id = :id")
  Optional<GuestSession> findByIdForUpdate(@Param("id") UUID id);
}
