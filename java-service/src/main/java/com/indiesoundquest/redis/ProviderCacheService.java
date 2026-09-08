package com.indiesoundquest.redis;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;
import java.util.Optional;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Service;

@Service
public class ProviderCacheService {
  private final StringRedisTemplate redis;
  private final RedisCacheProperties properties;

  public ProviderCacheService(StringRedisTemplate redis, RedisCacheProperties properties) {
    this.redis = redis;
    this.properties = properties;
  }

  public Optional<String> get(String provider, String requestFingerprint) {
    var value = redis.opsForValue().get(RedisKeyNames.providerCache(provider, requestFingerprint));
    return value == null || value.isBlank() ? Optional.empty() : Optional.of(value);
  }

  public void put(String provider, String requestFingerprint, String responseBody) {
    if (responseBody == null || responseBody.isBlank()) {
      return;
    }
    redis.opsForValue().set(
        RedisKeyNames.providerCache(provider, requestFingerprint),
        responseBody,
        properties.getProviderCacheTtl());
  }

  public static String fingerprint(String value) {
    try {
      var digest = MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8));
      return HexFormat.of().formatHex(digest);
    } catch (NoSuchAlgorithmException exception) {
      throw new IllegalStateException("SHA-256 unavailable", exception);
    }
  }
}
