FROM maven:3.9-eclipse-temurin-21 AS build
WORKDIR /workspace
COPY pom.xml ./
COPY .mvn/settings.xml /root/.m2/settings.xml
RUN --mount=type=cache,target=/root/.m2 \
    mvn -q -DskipTests -Dmaven.wagon.http.retryHandler.count=5 dependency:go-offline
COPY src ./src
RUN --mount=type=cache,target=/root/.m2 \
    mvn -q -DskipTests -Dmaven.wagon.http.retryHandler.count=5 package

FROM eclipse-temurin:21-jre-alpine AS local-runtime
WORKDIR /app
COPY target/java-service-0.1.0-SNAPSHOT.jar app.jar
EXPOSE 8080
ENTRYPOINT ["java", "-jar", "/app/app.jar"]

FROM eclipse-temurin:21-jre-alpine AS runtime
WORKDIR /app
COPY --from=build /workspace/target/java-service-0.1.0-SNAPSHOT.jar app.jar
EXPOSE 8080
ENTRYPOINT ["java", "-jar", "/app/app.jar"]
