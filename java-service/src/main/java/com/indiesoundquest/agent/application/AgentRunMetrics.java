package com.indiesoundquest.agent.application;

import com.indiesoundquest.agent.domain.AgentRunType;
import io.micrometer.core.instrument.MeterRegistry;
import java.time.Duration;
import org.springframework.stereotype.Component;

@Component
public class AgentRunMetrics {
  private final MeterRegistry registry;
  public AgentRunMetrics(MeterRegistry registry){this.registry=registry;}
  public void created(AgentRunType type){registry.counter("isq.agent.runs.created","type",type.name()).increment();}
  public void claimed(AgentRunType type){registry.counter("isq.agent.runs.claimed","type",type.name()).increment();}
  public void retry(String reason){registry.counter("isq.agent.runs.retries","reason",reason).increment();}
  public void completed(AgentRunType type,Duration duration){registry.counter("isq.agent.runs.completed","type",type.name()).increment();registry.timer("isq.agent.runs.duration","type",type.name()).record(duration);}
  public void failed(AgentRunType type){registry.counter("isq.agent.runs.failed","type",type.name()).increment();}
}
