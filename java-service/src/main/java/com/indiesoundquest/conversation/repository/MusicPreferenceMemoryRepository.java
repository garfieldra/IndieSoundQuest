package com.indiesoundquest.conversation.repository;
import com.indiesoundquest.conversation.domain.MusicPreferenceMemory; import java.util.*; import org.springframework.data.jpa.repository.JpaRepository;
public interface MusicPreferenceMemoryRepository extends JpaRepository<MusicPreferenceMemory,UUID> {
 List<MusicPreferenceMemory> findByGuestSessionIdAndConfirmationStatusOrderByUpdatedAtDesc(UUID guestSessionId,String confirmationStatus);
}
