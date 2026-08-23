package com.indiesoundquest.tournament.domain;

import jakarta.persistence.*;
import java.time.Instant;
import java.util.UUID;

@Entity @Table(name="tournament")
public class Tournament {
  @Id private UUID id;
  @ManyToOne(fetch=FetchType.LAZY) @JoinColumn(name="guest_session_id", nullable=false) private GuestSession guestSession;
  @ManyToOne(fetch=FetchType.LAZY) @JoinColumn(name="artist_id") private Artist artist;
  @Enumerated(EnumType.STRING) @Column(name="candidate_source",nullable=false) private CandidateSource candidateSource;
  @Column(name="exploration_brief") private String explorationBrief;
  @Column(name="agent_guidance_requested",nullable=false) private boolean agentGuidanceRequested;
  @Column(nullable=false) private int size;
  @Enumerated(EnumType.STRING) @Column(nullable=false) private TournamentStatus status;
  @Column(name="bracket_seed",nullable=false) private long bracketSeed;
  @Column(name="winner_entry_id") private UUID winnerEntryId;
  @Version private long version;
  @Column(name="created_at",nullable=false) private Instant createdAt;
  @Column(name="started_at") private Instant startedAt;
  @Column(name="completed_at") private Instant completedAt;
  @Column(name="deleted_at") private Instant deletedAt;
  @Column(name="creation_idempotency_key", columnDefinition="CHAR(36)") private String creationIdempotencyKey;
  @Column(name="creation_request_hash", columnDefinition="CHAR(64)") private String creationRequestHash;
  protected Tournament() {}
  public static Tournament draft(UUID id, GuestSession guest, Artist artist, int size, long seed, String idempotencyKey, String requestHash) {
    if (size != 16 && size != 32) throw new IllegalArgumentException("size must be 16 or 32");
    var t=new Tournament(); t.id=id; t.guestSession=guest; t.artist=artist; t.size=size; t.bracketSeed=seed;
    t.candidateSource=CandidateSource.POPULAR; t.status=TournamentStatus.DRAFT; t.createdAt=Instant.now(); t.creationIdempotencyKey=idempotencyKey; t.creationRequestHash=requestHash; return t;
  }
  public static Tournament agentDraft(UUID id, GuestSession guest, int size, long seed, String explorationBrief, String idempotencyKey, String requestHash) {
    if (size != 16 && size != 32) throw new IllegalArgumentException("size must be 16 or 32");
    var t=new Tournament(); t.id=id; t.guestSession=guest; t.size=size; t.bracketSeed=seed; t.candidateSource=CandidateSource.AGENT_GENERATED; t.explorationBrief=explorationBrief; t.agentGuidanceRequested=false; t.status=TournamentStatus.DRAFT; t.createdAt=Instant.now(); t.creationIdempotencyKey=idempotencyKey; t.creationRequestHash=requestHash; return t;
  }
  public void prepare() { if(status!=TournamentStatus.DRAFT) throw new IllegalStateException("tournament is not a draft"); status=TournamentStatus.READY; }
  public void startIfNeeded() { if(status==TournamentStatus.READY){status=TournamentStatus.IN_PROGRESS; startedAt=Instant.now();} }
  public void complete(UUID winner) { status=TournamentStatus.COMPLETED; winnerEntryId=winner; completedAt=Instant.now(); }
  public UUID getId(){return id;} public int getSize(){return size;} public long getBracketSeed(){return bracketSeed;} public TournamentStatus getStatus(){return status;} public UUID getWinnerEntryId(){return winnerEntryId;} public Instant getCompletedAt(){return completedAt;} public GuestSession getGuestSession(){return guestSession;} public CandidateSource getCandidateSource(){return candidateSource;} public String getCreationRequestHash(){return creationRequestHash;}
}
