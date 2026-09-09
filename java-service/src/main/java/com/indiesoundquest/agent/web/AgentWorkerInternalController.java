package com.indiesoundquest.agent.web;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.indiesoundquest.agent.application.AgentRunApplicationService;
import com.indiesoundquest.agent.domain.AgentRunEventType;
import com.indiesoundquest.conversation.application.*;
import com.indiesoundquest.tournament.application.CandidatePoolApplicationService;
import com.indiesoundquest.tournament.application.PreferenceReportApplicationService;
import java.util.*;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.*;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/internal/v1/agent-runs")
public class AgentWorkerInternalController {
  private final AgentRunApplicationService runs; private final ConversationApplicationService conversations; private final CandidatePoolApplicationService candidatePools; private final PreferenceReportApplicationService reports; private final ObjectMapper json; private final String token;
  public AgentWorkerInternalController(AgentRunApplicationService runs,ConversationApplicationService conversations,CandidatePoolApplicationService candidatePools,PreferenceReportApplicationService reports,ObjectMapper json,@Value("${agent.internal.service-token}")String token){this.runs=runs;this.conversations=conversations;this.candidatePools=candidatePools;this.reports=reports;this.json=json;this.token=token;}
  private void authorize(String value){if(!Objects.equals(value,"Bearer "+token))throw new org.springframework.web.server.ResponseStatusException(HttpStatus.UNAUTHORIZED);}

  @PostMapping("/{id}/claim") LeaseView claim(@PathVariable UUID id,@RequestHeader("Authorization")String auth,@RequestBody Worker body){authorize(auth);var lease=runs.claim(id,body.workerId());return new LeaseView(lease.attemptNo(),lease.leaseToken());}
  @PostMapping("/{id}/heartbeat") void heartbeat(@PathVariable UUID id,@RequestHeader("Authorization")String auth,@RequestHeader("X-Lease-Token")String lease){authorize(auth);runs.heartbeat(id,lease);}
  @PostMapping("/{id}/events") void event(@PathVariable UUID id,@RequestHeader("Authorization")String auth,@RequestHeader("X-Lease-Token")String lease,@RequestBody Event body){authorize(auth);runs.assertLease(id,lease);var type=switch(body.type()){case "plan_updated"->AgentRunEventType.PLAN_UPDATED;case "tool_started"->AgentRunEventType.TOOL_STARTED;case "tool_completed"->AgentRunEventType.TOOL_COMPLETED;case "tool_degraded"->AgentRunEventType.TOOL_DEGRADED;case "progress"->AgentRunEventType.PROGRESS;default->throw new IllegalArgumentException("UNSUPPORTED_AGENT_EVENT");};runs.appendEvent(id,type,body.payloadJson());}
  @PostMapping("/{id}/complete") void complete(@PathVariable UUID id,@RequestHeader("Authorization")String auth,@RequestHeader("X-Lease-Token")String lease,@RequestBody Completion body){authorize(auth);runs.assertLease(id,lease);var card=body.card()==null?null:new ConversationAgentGateway.Card(body.card().messageType(),body.card().cardType(),write(body.card().payload()));var result=new ConversationAgentGateway.Result(body.text(),card);conversations.complete(body.conversationId(),id,result);runs.complete(id,body.card()==null?"{}":write(body.card().payload()));runs.finishLease(id,lease);}
  @PostMapping("/{id}/complete-candidate") void completeCandidate(@PathVariable UUID id,@RequestHeader("Authorization")String auth,@RequestHeader("X-Lease-Token")String lease,@RequestBody CandidateCompletion body){authorize(auth);runs.assertLease(id,lease);try{var normalized=candidatePools.fromAgentResult(id,body.size(),json.readTree(body.resultJson()),body.seedArtistIds());runs.complete(id,write(normalized));runs.finishLease(id,lease);}catch(Exception e){throw new IllegalArgumentException("CANDIDATE_RESULT_INVALID",e);}}
  @PostMapping("/{id}/report-started") void reportStarted(@PathVariable UUID id,@RequestHeader("Authorization")String auth,@RequestHeader("X-Lease-Token")String lease,@RequestBody ReportRef body){authorize(auth);reports.markAgentRunRunning(id,lease,body.reportId());}
  @PostMapping("/{id}/complete-report") void completeReport(@PathVariable UUID id,@RequestHeader("Authorization")String auth,@RequestHeader("X-Lease-Token")String lease,@RequestBody ReportCompletion body){authorize(auth);reports.completeAgentRun(id,lease,body.reportId(),body.resultJson());}
  @PostMapping("/{id}/fail-report") void failReport(@PathVariable UUID id,@RequestHeader("Authorization")String auth,@RequestHeader("X-Lease-Token")String lease,@RequestBody ReportFailure body){authorize(auth);reports.failAgentRun(id,lease,body.reportId(),body.code(),body.message());}
  @PostMapping("/{id}/fail") void fail(@PathVariable UUID id,@RequestHeader("Authorization")String auth,@RequestHeader("X-Lease-Token")String lease,@RequestBody Failure body){authorize(auth);runs.assertLease(id,lease);runs.fail(id,write(Map.of("code",body.code(),"message",body.message())));runs.finishLease(id,lease);}
  @PostMapping("/{id}/retry") void retry(@PathVariable UUID id,@RequestHeader("Authorization")String auth,@RequestHeader("X-Lease-Token")String lease,@RequestBody Failure body){authorize(auth);runs.retry(id,lease,body.code());}
  private String write(Object value){try{return json.writeValueAsString(value);}catch(Exception e){throw new IllegalArgumentException(e);}}
  @ExceptionHandler(IllegalStateException.class) ResponseEntity<Void> conflict(){return ResponseEntity.status(HttpStatus.CONFLICT).build();}
  record Worker(String workerId){} record LeaseView(int attemptNo,String leaseToken){} record Event(String type,String payloadJson){} record Card(String messageType,String cardType,Map<String,Object> payload){} record Completion(UUID conversationId,String text,Card card){} record CandidateCompletion(int size,List<UUID> seedArtistIds,String resultJson){} record ReportRef(UUID reportId){} record ReportCompletion(UUID reportId,String resultJson){} record ReportFailure(UUID reportId,String code,String message){} record Failure(String code,String message){}
}
