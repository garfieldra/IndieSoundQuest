package com.indiesoundquest.agent.repository;

import com.indiesoundquest.agent.domain.AgentRunFollowUp;
import java.util.Optional;
import java.util.UUID;
import jakarta.persistence.LockModeType;
import org.springframework.data.jpa.repository.*;
import org.springframework.data.repository.query.Param;

public interface AgentRunFollowUpRepository extends JpaRepository<AgentRunFollowUp, UUID> {
  Optional<AgentRunFollowUp> findByRunId(UUID runId);
  Optional<AgentRunFollowUp> findByRunIdAndClientMessageId(UUID runId, UUID clientMessageId);

  @Lock(LockModeType.PESSIMISTIC_WRITE)
  @Query("select f from AgentRunFollowUp f where f.runId=:runId")
  Optional<AgentRunFollowUp> findLockedByRunId(@Param("runId") UUID runId);
}
