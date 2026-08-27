FROM golang:1.26-bookworm AS douban
RUN go install github.com/tamnd/douban-cli/cmd/douban@v0.2.0
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY --from=douban /go/bin/douban /usr/local/bin/douban
COPY app ./app
RUN useradd --create-home --uid 10001 research && chown -R research:research /app
USER research
EXPOSE 8093
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8093"]
