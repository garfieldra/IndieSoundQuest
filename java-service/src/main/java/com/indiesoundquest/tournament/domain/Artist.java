package com.indiesoundquest.tournament.domain;

import jakarta.persistence.*;
import java.util.UUID;

@Entity
@Table(name = "artist")
public class Artist {
  @Id private UUID id;
  @Column(nullable = false) private String name;
  @Column(name = "sort_name", nullable = false) private String sortName;
  @Column(name = "musicbrainz_mbid", unique = true) private String musicbrainzMbid;

  protected Artist() {}

  public static Artist imported(String name, String sortName, String musicbrainzMbid) {
    var artist = new Artist();
    artist.id = UUID.randomUUID();
    artist.name = name;
    artist.sortName = sortName;
    artist.musicbrainzMbid = musicbrainzMbid;
    return artist;
  }

  public void attachMusicbrainzIdentity(String musicbrainzMbid) {
    if (this.musicbrainzMbid != null && !this.musicbrainzMbid.equals(musicbrainzMbid)) {
      throw new IllegalStateException("artist already has a different MusicBrainz identity");
    }
    this.musicbrainzMbid = musicbrainzMbid;
  }

  public UUID getId() { return id; }
  public String getName() { return name; }
  public String getSortName() { return sortName; }
  public String getMusicbrainzMbid() { return musicbrainzMbid; }
}
