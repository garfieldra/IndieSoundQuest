package com.indiesoundquest.tournament.domain;
import jakarta.persistence.*; import java.time.Instant; import java.util.UUID;
@Entity @Table(name="guest_session") public class GuestSession { @Id private UUID id; @Column(name="token_hash",nullable=false,unique=true,columnDefinition="CHAR(64)") private String tokenHash; @Column(name="created_at",nullable=false) private Instant createdAt; protected GuestSession(){} public GuestSession(UUID id,String hash){this.id=id;tokenHash=hash;createdAt=Instant.now();} public UUID getId(){return id;} }
