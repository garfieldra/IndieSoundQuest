package com.indiesoundquest.config;

import org.springframework.context.annotation.Configuration;
import org.springframework.web.servlet.config.annotation.AsyncSupportConfigurer;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

/** Keeps long-running, progress-producing Agent SSE requests alive beyond the
 * servlet container's default async timeout. The Agent still enforces its own
 * 15-minute deadline, so this is not an unbounded request. */
@Configuration
public class AsyncWebConfig implements WebMvcConfigurer {
  @Override public void configureAsyncSupport(AsyncSupportConfigurer configurer) {
    configurer.setDefaultTimeout(900_000L);
  }
}
