import pytest
from qdrant_client import QdrantClient

from app.qdrant_store import bootstrap_collections


@pytest.fixture
def qdrant() -> QdrantClient:
    client = QdrantClient(":memory:")
    bootstrap_collections(client, embed_dim=384)
    return client
