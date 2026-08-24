package com.indiesoundquest.conversation.repository;
import com.indiesoundquest.conversation.domain.*; import java.util.*; import org.springframework.data.jpa.repository.JpaRepository;
public interface ConversationRepository extends JpaRepository<Conversation,UUID>{ Optional<Conversation> findByIdAndGuestSessionIdAndStatusNot(UUID id,UUID guestId,ConversationStatus status); List<Conversation> findByGuestSessionIdAndStatusNotOrderByLastMessageAtDesc(UUID guestId,ConversationStatus status); }
