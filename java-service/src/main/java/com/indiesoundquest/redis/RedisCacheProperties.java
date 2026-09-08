package com.indiesoundquest.redis;

import java.time.Duration;
import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "indie.redis")
public class RedisCacheProperties {
  private Duration idempotencyTtl = Duration.ofHours(24);
  private Duration providerCacheTtl = Duration.ofHours(6);
  private Duration tournamentStateTtl = Duration.ofMinutes(5);
  private RateLimit rateLimit = new RateLimit();

  public Duration getIdempotencyTtl() {
    return idempotencyTtl;
  }

  public void setIdempotencyTtl(Duration idempotencyTtl) {
    this.idempotencyTtl = idempotencyTtl;
  }

  public Duration getProviderCacheTtl() {
    return providerCacheTtl;
  }

  public void setProviderCacheTtl(Duration providerCacheTtl) {
    this.providerCacheTtl = providerCacheTtl;
  }

  public Duration getTournamentStateTtl() {
    return tournamentStateTtl;
  }

  public void setTournamentStateTtl(Duration tournamentStateTtl) {
    this.tournamentStateTtl = tournamentStateTtl;
  }

  public RateLimit getRateLimit() {
    return rateLimit;
  }

  public void setRateLimit(RateLimit rateLimit) {
    this.rateLimit = rateLimit;
  }

  public static class RateLimit {
    private int tournamentCreatePerMinute = 10;
    private int votePerMinute = 120;
    private int reportPerMinute = 5;
    private int conversationMessagePerMinute = 30;
    private int explorationReportPerMinute = 5;
    private int agentRunAnswerPerMinute = 20;

    public int getTournamentCreatePerMinute() {
      return tournamentCreatePerMinute;
    }

    public void setTournamentCreatePerMinute(int tournamentCreatePerMinute) {
      this.tournamentCreatePerMinute = tournamentCreatePerMinute;
    }

    public int getVotePerMinute() {
      return votePerMinute;
    }

    public void setVotePerMinute(int votePerMinute) {
      this.votePerMinute = votePerMinute;
    }

    public int getReportPerMinute() {
      return reportPerMinute;
    }

    public void setReportPerMinute(int reportPerMinute) {
      this.reportPerMinute = reportPerMinute;
    }

    public int getConversationMessagePerMinute() {
      return conversationMessagePerMinute;
    }

    public void setConversationMessagePerMinute(int conversationMessagePerMinute) {
      this.conversationMessagePerMinute = conversationMessagePerMinute;
    }

    public int getExplorationReportPerMinute() {
      return explorationReportPerMinute;
    }

    public void setExplorationReportPerMinute(int explorationReportPerMinute) {
      this.explorationReportPerMinute = explorationReportPerMinute;
    }

    public int getAgentRunAnswerPerMinute() {
      return agentRunAnswerPerMinute;
    }

    public void setAgentRunAnswerPerMinute(int agentRunAnswerPerMinute) {
      this.agentRunAnswerPerMinute = agentRunAnswerPerMinute;
    }
  }
}
