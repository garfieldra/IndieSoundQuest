package com.indiesoundquest.redis;

import java.util.Optional;
import java.util.UUID;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Service;

@Service
public class TournamentStateCacheService {
  private final StringRedisTemplate redis;
  private final RedisCacheProperties properties;

  public TournamentStateCacheService(StringRedisTemplate redis, RedisCacheProperties properties) {
    this.redis = redis;
    this.properties = properties;
  }

  public Optional<String> get(UUID tournamentId) {
    var value = redis.opsForValue().get(RedisKeyNames.tournamentState(tournamentId));
    return value == null || value.isBlank() ? Optional.empty() : Optional.of(value);
  }

  public void put(UUID tournamentId, String json) {
    redis.opsForValue().set(RedisKeyNames.tournamentState(tournamentId), json, properties.getTournamentStateTtl());
  }

  public void invalidate(UUID tournamentId) {
    redis.delete(RedisKeyNames.tournamentState(tournamentId));
  }
}
