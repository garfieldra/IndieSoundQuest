package com.indiesoundquest.redis;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.time.Duration;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.ValueOperations;

class IdempotencyCacheServiceTest {
  private final StringRedisTemplate redis = mock(StringRedisTemplate.class);
  private final ValueOperations<String, String> values = mock(ValueOperations.class);
  private final ObjectMapper objectMapper = new ObjectMapper();
  private IdempotencyCacheService service;

  @BeforeEach
  void setUp() {
    when(redis.opsForValue()).thenReturn(values);
    var properties = new RedisCacheProperties();
    properties.setIdempotencyTtl(Duration.ofHours(1));
    service = new IdempotencyCacheService(redis, objectMapper, properties);
  }

  @Test
  void storesAndReadsIdempotencyEntry() throws Exception {
    var guestId = UUID.randomUUID();
    var key = UUID.randomUUID().toString();
    var payload = "{\"requestHash\":\"abc\",\"payload\":\"tournament-id\"}";
    when(values.get(RedisKeyNames.idempotency(guestId, IdempotencyOperations.CREATE_TOURNAMENT, key))).thenReturn(payload);

    var entry = service.get(guestId, IdempotencyOperations.CREATE_TOURNAMENT, key);

    assertThat(entry).contains(new IdempotencyCacheService.Entry("abc", "tournament-id"));
  }

  @Test
  void writesEntryWithConfiguredTtl() throws Exception {
    var guestId = UUID.randomUUID();
    var key = UUID.randomUUID().toString();
    var redisKeyCaptor = ArgumentCaptor.forClass(String.class);

    service.put(guestId, IdempotencyOperations.VOTE, key, "hash", "payload");

    verify(values).set(redisKeyCaptor.capture(), contains("\"payload\":\"payload\""), eq(Duration.ofHours(1)));
    assertThat(redisKeyCaptor.getValue()).isEqualTo(RedisKeyNames.idempotency(guestId, IdempotencyOperations.VOTE, key));
  }

  @Test
  void providerFingerprintIsStable() {
    assertThat(ProviderCacheService.fingerprint("recording?query=foo"))
        .isEqualTo(ProviderCacheService.fingerprint("recording?query=foo"))
        .isNotEqualTo(ProviderCacheService.fingerprint("recording?query=bar"));
  }
}
