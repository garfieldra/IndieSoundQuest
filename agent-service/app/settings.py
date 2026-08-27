from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    agent_internal_service_token: str = "change-me"
    java_internal_base_url: str = "http://java-service:8080"
    llm_provider: str = "deepseek"
    llm_model: str = "deepseek-chat"
    deepseek_api_key: str | None = None
    tavily_api_key: str | None = None
    bocha_api_key: str | None = None
    spotify_discovery_enabled: bool = False
    spotify_client_id: str | None = None
    spotify_client_secret: str | None = None
    spotify_market: str | None = None
    milvus_uri: str = "http://milvus:19530"
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    knowledge_collection: str = "isq_song_theme_cards_v1"
    agent_max_tool_rounds: int = 6
    # Domestic community research is opt-in.  Keeping it disabled makes a
    # development boot deterministic and avoids accidental account-dependent
    # traffic when the sidecars are not started.
    zhihu_research_enabled: bool = False
    bilibili_research_enabled: bool = False
    douban_research_enabled: bool = False
    zhihu_research_base_url: str = "http://zhihu-research:8091"
    bilibili_research_base_url: str = "http://bilibili-research:8092"
    douban_research_base_url: str = "http://douban-research:8093"
    domestic_research_max_calls: int = 4
    domestic_research_timeout_seconds: int = 12
    domestic_research_max_sources: int = 8


settings = Settings()
