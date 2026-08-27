package com.indiesoundquest;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.scheduling.annotation.EnableScheduling;

@SpringBootApplication
@EnableScheduling
public class IndieSoundQuestApplication {
    public static void main(String[] args) {
        SpringApplication.run(IndieSoundQuestApplication.class, args);
    }
}
