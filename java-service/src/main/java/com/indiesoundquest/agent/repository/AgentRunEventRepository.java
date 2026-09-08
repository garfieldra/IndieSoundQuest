package com.indiesoundquest.agent.repository;
import com.indiesoundquest.agent.domain.AgentRunEvent; import java.util.*; import org.springframework.data.jpa.repository.JpaRepository;
public interface AgentRunEventRepository extends JpaRepository<AgentRunEvent,UUID> {
 List<AgentRunEvent> findByRunIdAndSequenceNumberGreaterThanOrderBySequenceNumberAsc(UUID runId,long afterSequence);
 Optional<AgentRunEvent> findTopByRunIdOrderBySequenceNumberDesc(UUID runId);
}
