package com.indiesoundquest.tournament.web;
import com.fasterxml.jackson.databind.ObjectMapper; import com.indiesoundquest.identity.GuestIdentityFilter; import com.indiesoundquest.tournament.application.MusicPreferenceProfileService; import com.indiesoundquest.tournament.domain.*; import jakarta.servlet.http.HttpServletRequest; import java.util.*; import org.springframework.http.*; import org.springframework.web.bind.annotation.*;
@RestController @RequestMapping("/api/v1/music-preference-profile") public class MusicPreferenceProfileController {
 private final MusicPreferenceProfileService service; private final ObjectMapper json; public MusicPreferenceProfileController(MusicPreferenceProfileService service,ObjectMapper json){this.service=service;this.json=json;}
 @GetMapping public ResponseEntity<?> get(HttpServletRequest r){return service.get(guest(r)).<ResponseEntity<?>>map(this::view).orElseGet(()->ResponseEntity.status(HttpStatus.NOT_FOUND).build());}
 @PostMapping("/refresh") public Map<String,Object> refresh(HttpServletRequest r){return view(service.refresh(guest(r))).getBody();}
 @PatchMapping("/settings") public Map<String,Object> settings(@RequestBody Settings body,HttpServletRequest r){return view(service.settings(guest(r),body.useForCandidateGeneration())).getBody();}
 @DeleteMapping public ResponseEntity<Void> reset(HttpServletRequest r){service.reset(guest(r));return ResponseEntity.noContent().build();}
 private ResponseEntity<Map<String,Object>> view(MusicPreferenceProfile p){try{return ResponseEntity.ok(Map.of("id",p.getId(),"tournamentCount",p.getTournamentCount(),"useForCandidateGeneration",p.isUseForCandidateGeneration(),"summaryStatus",p.getSummaryStatus(),"profile",json.readTree(p.getProfileJson())));}catch(Exception e){throw new IllegalStateException("PROFILE_READ_FAILED",e);}}
 private GuestSession guest(HttpServletRequest r){return (GuestSession)r.getAttribute(GuestIdentityFilter.ATTRIBUTE);} record Settings(boolean useForCandidateGeneration){}
}
