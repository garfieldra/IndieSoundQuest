package com.indiesoundquest.conversation.application;

import static org.junit.jupiter.api.Assertions.*;

import com.indiesoundquest.conversation.domain.ConversationMessage;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class ConversationMemoryPolicyTest {
  @Test
  void compressesAtTwentyUserTurnsOrEmergencyTokenBudget() {
    assertFalse(ConversationApplicationService.shouldCompress(19, 849_999));
    assertTrue(ConversationApplicationService.shouldCompress(20, 1));
    assertTrue(ConversationApplicationService.shouldCompress(1, 850_000));
  }

  @Test
  void progressUsesTheThresholdThatIsCloserToBeingReached() {
    assertEquals(20, ConversationApplicationService.memoryProgressPercent(2, 170_000));
    assertEquals(50, ConversationApplicationService.memoryProgressPercent(10, 1));
    assertEquals(100, ConversationApplicationService.memoryProgressPercent(21, 1));
    assertEquals(100, ConversationApplicationService.memoryProgressPercent(1, 900_000));
  }

  @Test
  void keepsTheLatestSixCompleteTurns() {
    UUID conversationId = UUID.randomUUID();
    List<ConversationMessage> messages = new ArrayList<>();
    long sequence = 1;
    messages.add(ConversationMessage.system(conversationId, "开始", sequence++));
    for (int turn = 1; turn <= 20; turn++) {
      messages.add(ConversationMessage.user(conversationId, UUID.randomUUID(), "用户 " + turn, sequence++));
      messages.add(ConversationMessage.assistant(conversationId, UUID.randomUUID(), "回答 " + turn, sequence++));
    }

    int retainedStart = ConversationApplicationService.retainedStartForTurns(messages, 6);
    List<ConversationMessage> retained = messages.subList(retainedStart, messages.size());

    assertEquals("用户 15", retained.getFirst().getTextContent());
    assertEquals(6, retained.stream().filter(message -> message.getRole().name().equals("USER")).count());
    assertEquals("回答 20", retained.getLast().getTextContent());
  }
}
