package com.indiesoundquest.listening;

import java.util.UUID;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/recordings")
public class ListeningOptionsController {
  private final ListeningOptionsService service;

  public ListeningOptionsController(ListeningOptionsService service) { this.service = service; }

  @GetMapping("/{recordingId}/listening-options")
  ListeningOptionsService.ListeningOptions get(@PathVariable UUID recordingId) {
    return service.get(recordingId);
  }
}

