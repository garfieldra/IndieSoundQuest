package com.indiesoundquest.conversation.web;
import com.indiesoundquest.conversation.application.MusicPreferenceMemoryService; import com.indiesoundquest.conversation.domain.MusicPreferenceMemory; import com.indiesoundquest.identity.GuestIdentityFilter; import com.indiesoundquest.tournament.domain.GuestSession; import jakarta.servlet.http.HttpServletRequest; import java.util.*; import org.springframework.http.*; import org.springframework.web.bind.annotation.*;
@RestController @RequestMapping("/api/v1/music-preference-memories") public class MusicPreferenceMemoryController {
 private final MusicPreferenceMemoryService service;
 public MusicPreferenceMemoryController(MusicPreferenceMemoryService service){this.service=service;}
 @GetMapping List<View> list(HttpServletRequest request){var guest=((GuestSession)request.getAttribute(GuestIdentityFilter.ATTRIBUTE)).getId();return service.list(guest).stream().map(View::of).toList();}
 @DeleteMapping("/{id}") ResponseEntity<Void> delete(@PathVariable UUID id,HttpServletRequest request){service.revoke(((GuestSession)request.getAttribute(GuestIdentityFilter.ATTRIBUTE)).getId(),id);return ResponseEntity.noContent().build();}
 record View(UUID id,String content,UUID sourceConversationId){static View of(MusicPreferenceMemory memory){return new View(memory.getId(),memory.getContent(),memory.getSourceConversationId());}}
}
