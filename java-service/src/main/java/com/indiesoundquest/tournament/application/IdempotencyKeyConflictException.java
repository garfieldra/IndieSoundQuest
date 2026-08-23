package com.indiesoundquest.tournament.application;

public class IdempotencyKeyConflictException extends RuntimeException {
  public IdempotencyKeyConflictException() {
    super("同一个 Idempotency-Key 不能用于不同的赛事创建请求");
  }
}
