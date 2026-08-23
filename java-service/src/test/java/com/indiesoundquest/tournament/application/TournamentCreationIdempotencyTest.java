package com.indiesoundquest.tournament.application;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

import com.indiesoundquest.tournament.domain.*;
import com.indiesoundquest.tournament.repository.*;
import java.util.*;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

class TournamentCreationIdempotencyTest {
  private final TournamentRepository tournaments = mock(TournamentRepository.class);
  private final TournamentEntryRepository entries = mock(TournamentEntryRepository.class);
  private final TournamentMatchRepository matches = mock(TournamentMatchRepository.class);
  private final VoteRepository votes = mock(VoteRepository.class);
  private final MusicPreferenceProfileService profiles = mock(MusicPreferenceProfileService.class);
  private final GuestSessionRepository guests = mock(GuestSessionRepository.class);
  private TournamentApplicationService service;
  private GuestSession guest;
  private List<Recording> recordings;

  @BeforeEach
  void setUp() {
    service = new TournamentApplicationService(tournaments, entries, matches, votes, profiles, guests);
    guest = new GuestSession(UUID.randomUUID(), "a".repeat(64));
    var artist = Artist.imported("测试艺人", "测试艺人", UUID.randomUUID().toString());
    recordings = new ArrayList<>();
    for (int index = 0; index < 16; index++) {
      recordings.add(Recording.imported(artist, "测试歌曲 " + index, "测试专辑", index + 1, UUID.randomUUID().toString(), null, "https://example.com/source"));
    }
    when(guests.findByIdForUpdate(guest.getId())).thenReturn(Optional.of(guest));
  }

  @Test
  void sameGuestKeyAndPayloadReturnsOriginalTournament() {
    var stored = new AtomicReference<Tournament>();
    when(tournaments.findByGuestSessionIdAndCreationIdempotencyKey(eq(guest.getId()), anyString()))
        .thenAnswer(invocation -> Optional.ofNullable(stored.get()));
    when(tournaments.save(any(Tournament.class))).thenAnswer(invocation -> {
      var tournament = invocation.getArgument(0, Tournament.class);
      stored.set(tournament);
      return tournament;
    });

    var key = UUID.randomUUID().toString();
    var first = service.createAgentDraft(guest, recordings, 16, "相同方向", key);
    var replay = service.createAgentDraft(guest, recordings, 16, "相同方向", key);

    assertThat(first.replayed()).isFalse();
    assertThat(replay.replayed()).isTrue();
    assertThat(replay.tournament().getId()).isEqualTo(first.tournament().getId());
    verify(tournaments, times(1)).save(any(Tournament.class));
    verify(entries, times(1)).saveAll(any());
  }

  @Test
  void sameKeyWithDifferentPayloadIsRejected() {
    var stored = new AtomicReference<Tournament>();
    when(tournaments.findByGuestSessionIdAndCreationIdempotencyKey(eq(guest.getId()), anyString()))
        .thenAnswer(invocation -> Optional.ofNullable(stored.get()));
    when(tournaments.save(any(Tournament.class))).thenAnswer(invocation -> {
      var tournament = invocation.getArgument(0, Tournament.class);
      stored.set(tournament);
      return tournament;
    });

    var key = UUID.randomUUID().toString();
    service.createAgentDraft(guest, recordings, 16, "方向 A", key);

    assertThatThrownBy(() -> service.createAgentDraft(guest, recordings, 16, "方向 B", key))
        .isInstanceOf(IdempotencyKeyConflictException.class);
    verify(tournaments, times(1)).save(any(Tournament.class));
    verify(entries, times(1)).saveAll(any());
  }
}
