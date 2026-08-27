package com.indiesoundquest.async;
import java.time.Instant; import java.util.*; import org.springframework.data.jpa.repository.*;
public interface AsyncOutboxEventRepository extends JpaRepository<AsyncOutboxEvent,UUID>{ List<AsyncOutboxEvent> findTop20ByStatusAndNextAttemptAtLessThanEqualOrderByCreatedAtAsc(String status, Instant now); }
