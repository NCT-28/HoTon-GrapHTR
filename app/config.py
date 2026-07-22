from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    qdrant_url: str = "http://localhost:6333"
    embed_model_name: str = "sentence-transformers/all-MiniLM-L6-v2"
    embed_dim: int = 384
    reasoning_model_name: str = "Qwen/Qwen2.5-1.5B-Instruct"
    searxng_url: str = "http://localhost:8888"
    browser_service_url: str = "http://localhost:8090"
    rag_port: int = 8030
    chunk_size: int = 1800
    chunk_overlap: int = 180
    neo4j_url: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "changeme"
    code_repos_dir: str = "./repos"
    usage_db_url: str = ""
    dashboard_user: str = ""
    dashboard_password: str = ""

    class Config:
        env_file = ".env"


@lru_cache
def get_settings() -> Settings:
    return Settings()
