FROM python:3.12-slim

WORKDIR /app
COPY install-official-cli.sh /usr/local/bin/install-official-cli
RUN chmod 755 /usr/local/bin/install-official-cli \
    && /usr/local/bin/install-official-cli
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
RUN useradd --create-home --uid 10001 research \
    && chown -R research:research /app
EXPOSE 8091
USER research
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8091"]
