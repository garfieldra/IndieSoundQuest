package com.indiesoundquest.conversation.domain;
import jakarta.persistence.*; import java.time.Instant; import java.util.UUID;
@Entity @Table(name="music_preference_memory") public class MusicPreferenceMemory {
 @Id private UUID id; @Column(name="guest_session_id",nullable=false) private UUID guestSessionId; @Column(nullable=false,length=800) private String content;
 @Column(name="source_conversation_id",nullable=false) private UUID sourceConversationId; @Column(name="confirmation_status",nullable=false) private String confirmationStatus;
 @Column(name="created_at",nullable=false) private Instant createdAt; @Column(name="updated_at",nullable=false) private Instant updatedAt; @Column(name="revoked_at") private Instant revokedAt;
 protected MusicPreferenceMemory(){}
 public static MusicPreferenceMemory confirmed(UUID guestSessionId,UUID sourceConversationId,String content){var memory=new MusicPreferenceMemory();memory.id=UUID.randomUUID();memory.guestSessionId=guestSessionId;memory.sourceConversationId=sourceConversationId;memory.content=content;memory.confirmationStatus="CONFIRMED";memory.createdAt=memory.updatedAt=Instant.now();return memory;}
 public UUID getId(){return id;} public UUID getGuestSessionId(){return guestSessionId;} public UUID getSourceConversationId(){return sourceConversationId;} public String getContent(){return content;} public String getConfirmationStatus(){return confirmationStatus;}
 public void revoke(){confirmationStatus="REVOKED";revokedAt=updatedAt=Instant.now();}
}
