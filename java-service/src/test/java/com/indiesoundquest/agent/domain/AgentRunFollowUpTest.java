package com.indiesoundquest.agent.domain;

import static org.junit.jupiter.api.Assertions.*;

import java.util.UUID;
import org.junit.jupiter.api.Test;

class AgentRunFollowUpTest {
  @Test
  void waitingMessageCanBeDispatchedOnlyOnce() {
    var run = AgentRun.queued(UUID.randomUUID(), UUID.randomUUID(), UUID.randomUUID(), AgentRunType.CONVERSATION, "{}");
    var followUp = AgentRunFollowUp.waiting(run, UUID.randomUUID(), "下一轮再谈现场版本");
    var nextRunId = UUID.randomUUID();

    followUp.dispatch(nextRunId);

    assertEquals(AgentRunFollowUpStatus.DISPATCHED, followUp.getStatus());
    assertEquals(nextRunId, followUp.getNextRunId());
    assertNotNull(followUp.getResolvedAt());
    assertThrows(IllegalStateException.class, () -> followUp.dispatch(UUID.randomUUID()));
  }

  @Test
  void waitingMessageCanBecomeTheSingleRuntimeIntervention() {
    var run = AgentRun.queued(UUID.randomUUID(), UUID.randomUUID(), UUID.randomUUID(), AgentRunType.CONVERSATION, "{}");
    var followUp = AgentRunFollowUp.waiting(run, UUID.randomUUID(), "现在就改为比较编曲差异");

    followUp.intervene();

    assertEquals(AgentRunFollowUpStatus.INTERVENED, followUp.getStatus());
    assertThrows(IllegalStateException.class, followUp::intervene);
  }
}
