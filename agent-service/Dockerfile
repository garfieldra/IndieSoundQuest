FROM python:3.12-slim AS base

WORKDIR /app
COPY pyproject.toml ./
# 依赖与业务源码分层：日常改 Agent 逻辑时，不必重新下载 LangChain/LangGraph。
RUN pip install --no-cache-dir \
    "fastapi>=0.115,<1" "uvicorn[standard]>=0.32,<1" \
    "pydantic-settings>=2.6,<3" "httpx>=0.27,<1" \
    "langchain-core>=0.3,<1" "langchain-openai>=0.2,<1" "langgraph>=0.2,<1" \
    "pymilvus>=2.5,<3" "fastembed>=0.4,<1" \
    "opentelemetry-api>=1.28,<2" "opentelemetry-sdk>=1.28,<2" "opentelemetry-exporter-otlp-proto-http>=1.28,<2" "opentelemetry-instrumentation-fastapi>=0.49b0,<1"
COPY app ./app
RUN pip install --no-cache-dir --no-deps .

EXPOSE 8000

FROM base AS test
RUN pip install --no-cache-dir "pytest>=8.3,<9" "pytest-asyncio>=0.24,<1"
COPY tests ./tests
CMD ["pytest", "-q"]

FROM base AS runtime
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
