package com.indiesoundquest.redis;

import java.util.UUID;

public final class RedisKeyNames {
  public static final String IDEM_PREFIX = "idem:";
  public static final String CACHE_PROVIDER_PREFIX = "cache:provider:";
  public static final String STATE_TOURNAMENT_PREFIX = "state:tournament:";
  public static final String RATE_LIMIT_PREFIX = "ratelimit:";

  private RedisKeyNames() {}

  public static String idempotency(UUID guestId, String operation, String key) {
    return IDEM_PREFIX + guestId + ":" + operation + ":" + key;
  }

  public static String providerCache(String provider, String hash) {
    return CACHE_PROVIDER_PREFIX + provider + ":" + hash;
  }

  public static String tournamentState(UUID tournamentId) {
    return STATE_TOURNAMENT_PREFIX + tournamentId;
  }

  public static String rateLimit(UUID guestId, String operation, long windowBucket) {
    return RATE_LIMIT_PREFIX + guestId + ":" + operation + ":" + windowBucket;
  }
}
