from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    agent_internal_service_token: str = "change-me"
    java_internal_base_url: str = "http://java-service:8080"
    llm_provider: str = "deepseek"
    llm_model: str = "deepseek-chat"
    deepseek_api_key: str | None = None
    tavily_api_key: str | None = None
    milvus_uri: str = "http://milvus:19530"
    agent_max_tool_rounds: int = 6


settings = Settings()
