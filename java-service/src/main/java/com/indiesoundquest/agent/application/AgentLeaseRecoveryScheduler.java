package com.indiesoundquest.agent.application;

import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

@Component
public class AgentLeaseRecoveryScheduler {
  private final AgentRunApplicationService runs;
  public AgentLeaseRecoveryScheduler(AgentRunApplicationService runs){this.runs=runs;}
  @Scheduled(fixedDelayString="${async.agent.lease-scan-interval-ms:5000}")
  public void recover(){for(var attempt:runs.expiredLeases()){try{runs.expireLease(attempt.getRunId(),attempt.getLeaseToken());}catch(RuntimeException ignored){}}}
}
