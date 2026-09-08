package com.indiesoundquest.agent.repository;
import com.indiesoundquest.agent.domain.PreferenceEvent; import java.util.*; import org.springframework.data.jpa.repository.JpaRepository;
public interface PreferenceEventRepository extends JpaRepository<PreferenceEvent,UUID> {
 Optional<PreferenceEvent> findByGuestSessionIdAndIdempotencyKey(UUID guestSessionId,String idempotencyKey);
 List<PreferenceEvent> findTop20ByGuestSessionIdOrderByCreatedAtDesc(UUID guestSessionId);
}
