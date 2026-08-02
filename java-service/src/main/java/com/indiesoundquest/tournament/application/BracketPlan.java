package com.indiesoundquest.tournament.application;

import java.util.List;
import java.util.UUID;

public record BracketPlan(List<PlannedMatch> matches) {
    public record PlannedMatch(UUID id, int roundNumber, int matchIndex, UUID leftEntryId,
                               UUID rightEntryId, UUID nextMatchId, String nextSlot) { }
}
