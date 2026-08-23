package com.indiesoundquest.tournament.web;

import com.indiesoundquest.identity.GuestIdentityFilter;
import com.indiesoundquest.agent.CandidatePoolGateway;
import com.indiesoundquest.tournament.application.CandidatePoolApplicationService;
import com.indiesoundquest.tournament.domain.GuestSession;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;
import java.util.List;
import java.util.UUID;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/candidate-pools")
public class CandidatePoolController {
  private final CandidatePoolApplicationService service;

  public CandidatePoolController(CandidatePoolApplicationService service) {
    this.service = service;
  }

  @PostMapping
  CandidatePoolApplicationService.CandidatePoolResponse generate(
      @RequestHeader("X-Request-Id") String rawRequestId,
      @Valid @RequestBody Request body,
      HttpServletRequest request) {
    var requestId = requestId(rawRequestId);
    var guest = (GuestSession) request.getAttribute(GuestIdentityFilter.ATTRIBUTE);
    var seedIds = body.seedArtistIds() == null ? List.<UUID>of() : List.copyOf(body.seedArtistIds());
    var confirmed = body.confirmedArtists() == null ? List.<CandidatePoolGateway.ConfirmedArtist>of() : body.confirmedArtists().stream().map(item -> new CandidatePoolGateway.ConfirmedArtist(item.mention(), item.mbid(), item.name())).toList();
    return service.generate(requestId, guest.getId().toString(), body.size(), body.preferenceText(), seedIds, confirmed);
  }

  private UUID requestId(String value) {
    try {
      return UUID.fromString(value);
    } catch (RuntimeException exception) {
      throw new IllegalArgumentException("X-Request-Id 必须是 UUID");
    }
  }

  record Request(
      List<@NotNull UUID> seedArtistIds,
      List<ConfirmedArtist> confirmedArtists,
      int size,
      @NotBlank @Size(min = 3, max = 1000) String preferenceText) {}
  record ConfirmedArtist(@NotBlank @Size(max = 120) String mention, @NotNull UUID mbid, @NotBlank @Size(max = 160) String name) {}
}
