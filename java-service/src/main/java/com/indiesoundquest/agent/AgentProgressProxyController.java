package com.indiesoundquest.agent;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.indiesoundquest.identity.GuestIdentityFilter;
import com.indiesoundquest.tournament.application.CandidatePoolApplicationService;
import com.indiesoundquest.tournament.domain.GuestSession;
import jakarta.servlet.http.HttpServletRequest;
import java.io.BufferedReader;
import java.io.OutputStream;
import java.net.URI;
import java.net.http.*;
import java.time.Duration;
import java.util.*;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.MediaType;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.servlet.mvc.method.annotation.StreamingResponseBody;

/** Public, guest-scoped SSE bridge.  Python stays on the internal network. */
@RestController
@RequestMapping("/api/v1/agent-runs")
public class AgentProgressProxyController {
  private static final Logger log=LoggerFactory.getLogger(AgentProgressProxyController.class);
  private final ObjectMapper json; private final String baseUrl, token; private final CandidatePoolApplicationService candidatePools;
  private final HttpClient client=HttpClient.newBuilder().version(HttpClient.Version.HTTP_1_1).connectTimeout(Duration.ofSeconds(5)).build();
  public AgentProgressProxyController(ObjectMapper json, CandidatePoolApplicationService candidatePools, @Value("${agent.internal.base-url:http://agent-service:8000}") String baseUrl,@Value("${agent.internal.service-token}") String token){this.json=json;this.candidatePools=candidatePools;this.baseUrl=baseUrl;this.token=token;}

  @PostMapping(value="/candidate-pool:stream",produces=MediaType.TEXT_EVENT_STREAM_VALUE)
  StreamingResponseBody candidate(@RequestHeader("X-Request-Id") UUID requestId,@RequestBody CandidateBody body,HttpServletRequest request){
    var guest=((GuestSession)request.getAttribute(GuestIdentityFilter.ATTRIBUTE)).getId();
    var seeds=Optional.ofNullable(body.seedArtistIds()).orElse(List.of());
    return output -> relay(requestId, body.size(), seeds, Map.of("requestId",requestId,"guestId",guest,"size",body.size(),"candidateCount",body.size()*2,"preferenceText",body.preferenceText(),"seedArtistIds",seeds,"confirmedArtists",Optional.ofNullable(body.confirmedArtists()).orElse(List.of()),"excludeRecordingIds",List.of()),output);
  }

  private void relay(UUID requestId,int size,List<UUID> seeds,Object body,OutputStream output){
    try{
      var request=HttpRequest.newBuilder(URI.create(baseUrl+"/internal/v1/workflows/candidate-pool:stream")).timeout(Duration.ofSeconds(900)).header("Authorization","Bearer "+token).header("X-Request-Id",requestId.toString()).header("Content-Type","application/json").POST(HttpRequest.BodyPublishers.ofString(json.writeValueAsString(body))).build();
      var response=client.send(request,HttpResponse.BodyHandlers.ofInputStream()); if(response.statusCode()!=200)throw new IllegalStateException("AGENT_HTTP_"+response.statusCode());
      try(var lines=new BufferedReader(new java.io.InputStreamReader(response.body()))){String line; String event=null,data=null; while((line=lines.readLine())!=null){
        if(line.startsWith("event: ")) event=line.substring(7).trim(); else if(line.startsWith("data: ")) data=line.substring(6).trim(); else if(line.isEmpty()) { if(("progress".equals(event) || "plan_updated".equals(event)) && data!=null) write(output,event,data); else if("result".equals(event) && data!=null) write(output,"result",json.writeValueAsString(candidatePools.fromAgentResult(requestId,size,json.readTree(data),seeds))); else if("error".equals(event) && data!=null) write(output,"error",data); event=null; data=null; }
      }}
    }catch(Exception ex){
      log.warn("Candidate SSE relay failed requestId={} type={} message={}",requestId,ex.getClass().getSimpleName(),ex.getMessage());
      try{output.write(("event: error\ndata: {\"code\":\"AGENT_STREAM_UNAVAILABLE\",\"message\":\"候选整理超时或被外部资料源中断；已完成的线索不会丢失，请重试或缩小赛事规模。\"}\n\n").getBytes(java.nio.charset.StandardCharsets.UTF_8));output.flush();}catch(Exception ignored){}
    }
  }
  private void write(OutputStream output,String event,String data) throws java.io.IOException { output.write(("event: "+event+"\ndata: "+data+"\n\n").getBytes(java.nio.charset.StandardCharsets.UTF_8)); output.flush(); }
  record CandidateBody(int size,String preferenceText,List<UUID> seedArtistIds,List<Map<String,Object>> confirmedArtists){}
}
