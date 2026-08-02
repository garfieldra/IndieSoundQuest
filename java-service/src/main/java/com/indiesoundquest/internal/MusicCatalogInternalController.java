package com.indiesoundquest.internal;

import com.indiesoundquest.tournament.repository.RecordingRepository;
import java.util.*;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;

@RestController @RequestMapping("/internal/v1/music-catalog")
public class MusicCatalogInternalController {
  private final RecordingRepository recordings; private final String token;
  public MusicCatalogInternalController(RecordingRepository recordings,@Value("${agent.internal.service-token:change-me}") String token){this.recordings=recordings;this.token=token;}
  @PostMapping("/search") Map<String,Object> search(@RequestHeader("Authorization") String authorization,@RequestBody SearchRequest request){
    if(!authorization.equals("Bearer "+token)) throw new org.springframework.web.server.ResponseStatusException(HttpStatus.UNAUTHORIZED);
    if(request.artistIds()==null||request.artistIds().isEmpty()) return Map.of("items",List.of());
    var items=recordings.findByArtistIdInOrderBySeedRankAsc(request.artistIds()).stream().limit(Math.min(request.limit()==null?80:request.limit(),80)).map(r->Map.<String,Object>of("id",r.getId(),"title",r.getTitle(),"artistId",r.getArtist().getId(),"artistName",r.getArtist().getName(),"albumTitle",Optional.ofNullable(r.getAlbumTitle()).orElse(""),"coverStatus",Optional.ofNullable(r.getCoverStatus()).orElse("UNAVAILABLE"))).toList(); return Map.of("items",items);
  }
  record SearchRequest(String query,List<UUID> artistIds,Integer limit){}
}
