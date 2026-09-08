package com.indiesoundquest.redis;

public class RateLimitExceededException extends RuntimeException {
  public RateLimitExceededException(String operation) {
    super("请求过于频繁，请稍后再试：" + operation);
  }
}
