from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    qdrant_url: str = "http://localhost:6333"
    embed_model_name: str = "sentence-transformers/all-MiniLM-L6-v2"
    embed_dim: int = 384
    reasoning_model_name: str = "Qwen/Qwen2.5-1.5B-Instruct"
    # Auto-unload the reasoning model after this long unused. Reloading costs a full
    # multi-GB pipeline() init while holding ReasoningLLM's generation lock, so this
    # wants to be longer than a typical gap between question batches in one session.
    reasoning_model_idle_unload_seconds: int = 1800
    searxng_url: str = "http://localhost:8888"
    browser_service_url: str = "http://localhost:8090"
    rag_port: int = 8030
    chunk_size: int = 1800
    chunk_overlap: int = 180
    neo4j_url: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    # No hardcoded default: an unset NEO4J_PASSWORD must fail auth against Neo4j,
    # not silently connect with a guessable credential.
    neo4j_password: SecretStr = SecretStr("")
    code_repos_dir: str = "./repos"
    usage_db_url: str = ""
    usage_db_host: str = ""
    usage_db_port: int = 5432
    usage_db_user: str = ""
    usage_db_password: SecretStr = SecretStr("")
    usage_db_name: str = "hoton_rag"
    dashboard_user: str = ""
    dashboard_password: SecretStr = SecretStr("")
    deploy_mode: str = "server"          # "server" (default, Qdrant/Neo4j/Postgres) | "local" (zero-service, file-backed)
    local_data_dir: str = "./graphtr-out"

    class Config:
        env_file = ".env"


@lru_cache
def get_settings() -> Settings:
    return Settings()
