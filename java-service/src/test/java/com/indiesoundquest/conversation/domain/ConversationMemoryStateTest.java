package com.indiesoundquest.conversation.domain;

import static org.junit.jupiter.api.Assertions.*;

import java.util.UUID;
import org.junit.jupiter.api.Test;

class ConversationMemoryStateTest {
  @Test
  void summaryCoverageOnlyMovesForwardAndVersionsEachAcceptedCompression() {
    var conversation = new Conversation(UUID.randomUUID(), UUID.randomUUID(), "memory test");

    conversation.updateSummary("第一段压缩记忆", 12);
    assertEquals(1, conversation.getSummaryVersion());
    assertEquals(12, conversation.getSummaryThroughSequence());
    assertNotNull(conversation.getSummaryUpdatedAt());

    conversation.updateSummary("过期结果", 8);
    assertEquals(1, conversation.getSummaryVersion());
    assertEquals("第一段压缩记忆", conversation.getSummary());

    conversation.updateSummary("第二段增量压缩记忆", 24);
    assertEquals(2, conversation.getSummaryVersion());
    assertEquals(24, conversation.getSummaryThroughSequence());
    assertEquals("第二段增量压缩记忆", conversation.getSummary());
  }
}
