package com.indiesoundquest.agent.web;
import com.fasterxml.jackson.databind.ObjectMapper; import com.indiesoundquest.agent.application.RecommendationFeedbackService; import com.indiesoundquest.agent.domain.PreferenceFeedback; import com.indiesoundquest.identity.GuestIdentityFilter; import com.indiesoundquest.tournament.domain.GuestSession; import jakarta.servlet.http.HttpServletRequest; import jakarta.validation.Valid; import jakarta.validation.constraints.*; import java.util.*; import org.springframework.http.*; import org.springframework.web.bind.annotation.*;
@RestController @RequestMapping("/api/v1/recommendation-feedback") public class RecommendationFeedbackController {
 private final RecommendationFeedbackService service; private final ObjectMapper json;
 public RecommendationFeedbackController(RecommendationFeedbackService service,ObjectMapper json){this.service=service;this.json=json;}
 @PostMapping ResponseEntity<Map<String,Object>> create(@RequestHeader("Idempotency-Key") String idempotencyKey,@Valid @RequestBody Body body,HttpServletRequest request){
  var guest=((GuestSession)request.getAttribute(GuestIdentityFilter.ATTRIBUTE)).getId();
  var saved=service.record(guest,body.conversationId(),body.agentRunId(),body.targetType(),body.targetRef(),body.feedback(),idempotencyKey);
  return ResponseEntity.status(HttpStatus.CREATED).body(Map.of("id",saved.getId(),"feedback",saved.getFeedback().name()));
 }
 record Body(UUID conversationId,UUID agentRunId,@NotBlank @Size(max=20) String targetType,@NotNull Map<String,Object> targetRef,@NotNull PreferenceFeedback feedback){}
}
