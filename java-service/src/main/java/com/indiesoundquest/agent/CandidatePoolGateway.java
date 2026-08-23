package com.indiesoundquest.agent;

import com.fasterxml.jackson.databind.JsonNode; import com.fasterxml.jackson.databind.ObjectMapper;
import java.net.URI; import java.net.http.*; import java.time.Duration; import java.util.List; import java.util.UUID;
import org.slf4j.Logger; import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value; import org.springframework.stereotype.Component;

@Component public class CandidatePoolGateway {
  private static final Logger log=LoggerFactory.getLogger(CandidatePoolGateway.class);
  private final ObjectMapper json; private final HttpClient client=HttpClient.newHttpClient(); private final String baseUrl,token;
  public CandidatePoolGateway(ObjectMapper json,@Value("${agent.internal.base-url:http://agent-service:8000}") String baseUrl,@Value("${agent.internal.service-token}") String token){this.json=json;this.baseUrl=baseUrl;this.token=token;}
  public JsonNode generate(UUID requestId,String guestId,int size,String preference,List<UUID> artistIds) { return generate(requestId, guestId, size, preference, artistIds, List.of()); }
  public JsonNode generate(UUID requestId,String guestId,int size,String preference,List<UUID> artistIds,List<ConfirmedArtist> confirmedArtists) {
    try {var body=json.createObjectNode();body.put("requestId",requestId.toString());body.put("guestId",guestId);body.put("size",size);body.put("candidateCount",size*2);body.put("preferenceText",preference);var artists=body.putArray("seedArtistIds");artistIds.forEach(id->artists.add(id.toString()));var confirmed=body.putArray("confirmedArtists");confirmedArtists.forEach(item->{var node=confirmed.addObject();node.put("mention",item.mention());node.put("mbid",item.mbid().toString());node.put("name",item.name());});body.putArray("excludeRecordingIds");
      var request=HttpRequest.newBuilder(URI.create(baseUrl+"/internal/v1/workflows/candidate-pool:stream")).version(HttpClient.Version.HTTP_1_1).timeout(Duration.ofSeconds(900)).header("Authorization","Bearer "+token).header("X-Request-Id",requestId.toString()).header("Content-Type","application/json").POST(HttpRequest.BodyPublishers.ofString(json.writeValueAsString(body))).build();
      var response=client.send(request,HttpResponse.BodyHandlers.ofString()); if(response.statusCode()!=200) throw new IllegalStateException("agent unavailable: status="+response.statusCode()+", body="+response.body());
      for(String block:response.body().split("\\n\\n")){if(block.startsWith("event: result")){var data=block.substring(block.indexOf("data: ")+6);return json.readTree(data);}} throw new IllegalStateException("agent returned no result");
    }catch(Exception e){log.error("candidate pool generation failed",e);throw new IllegalStateException("candidate pool generation failed",e);}
  }
  public record ConfirmedArtist(String mention, UUID mbid, String name) {}
}
