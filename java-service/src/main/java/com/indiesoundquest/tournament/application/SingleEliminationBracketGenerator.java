package com.indiesoundquest.tournament.application;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Random;
import java.util.UUID;

public final class SingleEliminationBracketGenerator {
    public BracketPlan generate(List<UUID> entryIds, long seed) {
        if (entryIds.size() != 16 && entryIds.size() != 32) throw new IllegalArgumentException("size must be 16 or 32");
        if (entryIds.stream().distinct().count() != entryIds.size()) throw new IllegalArgumentException("entries must be unique");
        var shuffled = new ArrayList<>(entryIds);
        Collections.shuffle(shuffled, new Random(seed));
        var rounds = new ArrayList<List<UUID>>();
        int count = shuffled.size() / 2;
        while (count > 0) { var ids = new ArrayList<UUID>(); for (int i=0;i<count;i++) ids.add(UUID.randomUUID()); rounds.add(ids); count /= 2; }
        var matches = new ArrayList<BracketPlan.PlannedMatch>();
        for (int round=0; round<rounds.size(); round++) for (int index=0; index<rounds.get(round).size(); index++) {
            UUID next = round + 1 < rounds.size() ? rounds.get(round + 1).get(index / 2) : null;
            String slot = next == null ? null : (index % 2 == 0 ? "LEFT" : "RIGHT");
            UUID left = round == 0 ? shuffled.get(index * 2) : null;
            UUID right = round == 0 ? shuffled.get(index * 2 + 1) : null;
            matches.add(new BracketPlan.PlannedMatch(rounds.get(round).get(index), round + 1, index, left, right, next, slot));
        }
        return new BracketPlan(List.copyOf(matches));
    }
}
