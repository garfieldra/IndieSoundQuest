package com.indiesoundquest.redis;

public final class IdempotencyOperations {
  public static final String CREATE_TOURNAMENT = "create-tournament";
  public static final String VOTE = "vote";
  public static final String CONVERSATION_MESSAGE = "conversation-message";
  public static final String EXPLORATION_REPORT = "exploration-report";
  public static final String AGENT_RUN_ANSWER = "agent-run-answer";
  public static final String RECOMMENDATION_FEEDBACK = "recommendation-feedback";

  private IdempotencyOperations() {}
}
