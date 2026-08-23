package com.indiesoundquest.listening;

import java.util.Optional;
import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;

public interface RecordingListeningAssetRepository extends JpaRepository<RecordingListeningAsset, UUID> {
  Optional<RecordingListeningAsset> findByRecordingIdAndProviderAndStorefront(
      UUID recordingId, String provider, String storefront);
}

