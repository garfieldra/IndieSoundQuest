package com.indiesoundquest.conversation.application;
import com.indiesoundquest.conversation.domain.MusicPreferenceMemory; import com.indiesoundquest.conversation.repository.MusicPreferenceMemoryRepository; import java.util.*; import org.springframework.stereotype.Service; import org.springframework.transaction.annotation.Transactional;
@Service public class MusicPreferenceMemoryService {
 private final MusicPreferenceMemoryRepository memories;
 public MusicPreferenceMemoryService(MusicPreferenceMemoryRepository memories){this.memories=memories;}
 @Transactional(readOnly=true) public List<String> confirmedContents(UUID guestSessionId){return memories.findByGuestSessionIdAndConfirmationStatusOrderByUpdatedAtDesc(guestSessionId,"CONFIRMED").stream().map(MusicPreferenceMemory::getContent).limit(20).toList();}
 @Transactional public MusicPreferenceMemory save(UUID guestSessionId,UUID conversationId,String content){return memories.save(MusicPreferenceMemory.confirmed(guestSessionId,conversationId,content));}
 @Transactional public void revoke(UUID guestSessionId,UUID memoryId){var memory=memories.findById(memoryId).filter(item->item.getConfirmationStatus().equals("CONFIRMED")).orElseThrow(NoSuchElementException::new);if(!memory.getGuestSessionId().equals(guestSessionId))throw new NoSuchElementException();memory.revoke();memories.save(memory);}
 @Transactional(readOnly=true) public List<MusicPreferenceMemory> list(UUID guestSessionId){return memories.findByGuestSessionIdAndConfirmationStatusOrderByUpdatedAtDesc(guestSessionId,"CONFIRMED");}
}
