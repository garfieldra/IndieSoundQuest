package com.indiesoundquest.tournament.application;

import static org.assertj.core.api.Assertions.assertThat;
import java.util.List;
import java.util.UUID;
import java.util.stream.IntStream;
import org.junit.jupiter.api.Test;

class SingleEliminationBracketGeneratorTest {
    private final SingleEliminationBracketGenerator generator = new SingleEliminationBracketGenerator();
    @Test void createsFifteenMatchesForSixteenEntries() {
        var plan = generator.generate(entries(16), 42L);
        assertThat(plan.matches()).hasSize(15);
        assertThat(plan.matches().stream().filter(m -> m.roundNumber() == 1)).hasSize(8);
        assertThat(plan.matches().stream().filter(m -> m.nextMatchId() == null)).hasSize(1);
    }
    @Test void createsThirtyOneMatchesForThirtyTwoEntries() { assertThat(generator.generate(entries(32), 42L).matches()).hasSize(31); }
    private List<UUID> entries(int size) { return IntStream.range(0,size).mapToObj(i -> UUID.nameUUIDFromBytes(("entry-"+i).getBytes())).toList(); }
}
