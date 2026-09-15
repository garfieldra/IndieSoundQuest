package com.indiesoundquest.agent.repository;

import com.indiesoundquest.agent.domain.AgentRunIntervention;
import java.util.*;
import org.springframework.data.jpa.repository.JpaRepository;

public interface AgentRunInterventionRepository extends JpaRepository<AgentRunIntervention,UUID> {
  boolean existsByRunId(UUID runId);
  Optional<AgentRunIntervention> findByRunIdAndClientMessageId(UUID runId,UUID clientMessageId);
  Optional<AgentRunIntervention> findTopByRunIdOrderBySequenceNumberDesc(UUID runId);
  List<AgentRunIntervention> findByRunIdAndSequenceNumberGreaterThanOrderBySequenceNumberAsc(UUID runId,long afterSequence);
  List<AgentRunIntervention> findByRunIdAndSequenceNumberLessThanEqualOrderBySequenceNumberAsc(UUID runId,long throughSequence);
}
