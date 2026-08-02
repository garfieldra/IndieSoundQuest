package com.indiesoundquest.tournament.domain;
import jakarta.persistence.*; import java.util.UUID;
@Entity @Table(name="artist") public class Artist { @Id private UUID id; @Column(nullable=false) private String name; @Column(name="sort_name",nullable=false) private String sortName; protected Artist(){} public UUID getId(){return id;} public String getName(){return name;} }
