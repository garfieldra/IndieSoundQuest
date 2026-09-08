package com.indiesoundquest.redis;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.Optional;
import java.util.UUID;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Service;

@Service
public class IdempotencyCacheService {
  private final StringRedisTemplate redis;
  private final ObjectMapper objectMapper;
  private final RedisCacheProperties properties;

  public IdempotencyCacheService(StringRedisTemplate redis, ObjectMapper objectMapper, RedisCacheProperties properties) {
    this.redis = redis;
    this.objectMapper = objectMapper;
    this.properties = properties;
  }

  public Optional<Entry> get(UUID guestId, String operation, String key) {
    var raw = redis.opsForValue().get(RedisKeyNames.idempotency(guestId, operation, key));
    if (raw == null || raw.isBlank()) {
      return Optional.empty();
    }
    try {
      return Optional.of(objectMapper.readValue(raw, Entry.class));
    } catch (Exception exception) {
      redis.delete(RedisKeyNames.idempotency(guestId, operation, key));
      return Optional.empty();
    }
  }

  public void put(UUID guestId, String operation, String key, String requestHash, String payload) {
    try {
      var value = objectMapper.writeValueAsString(new Entry(requestHash, payload));
      redis.opsForValue().set(
          RedisKeyNames.idempotency(guestId, operation, key),
          value,
          properties.getIdempotencyTtl());
    } catch (Exception exception) {
      throw new IllegalStateException("IDEMPOTENCY_CACHE_WRITE_FAILED", exception);
    }
  }

  public record Entry(String requestHash, String payload) {}
}
