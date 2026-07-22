import os

import pytest
from neo4j import GraphDatabase

from app.code_graph_store import Neo4jGraphStore

NEO4J_TEST_URL = os.environ.get("NEO4J_TEST_URL")

pytestmark = pytest.mark.skipif(not NEO4J_TEST_URL, reason="set NEO4J_TEST_URL to run against a live Neo4j instance")


@pytest.fixture
def neo4j_store():
    driver = GraphDatabase.driver(NEO4J_TEST_URL, auth=("neo4j", os.environ.get("NEO4J_TEST_PASSWORD", "changeme")))
    driver.execute_query("MATCH (n) WHERE n.user_id STARTS WITH 'test-' DETACH DELETE n")
    yield Neo4jGraphStore(driver)
    driver.execute_query("MATCH (n) WHERE n.user_id STARTS WITH 'test-' DETACH DELETE n")
    driver.close()


def test_upsert_and_get_subgraph_round_trips_through_real_neo4j(neo4j_store):
    neo4j_store.upsert_symbols([
        {"id": "int-a", "user_id": "test-u1", "repo_id": "test-r1", "kind": "function", "name": "foo",
         "file_path": "a.py", "start_line": 1, "end_line": 2, "language": "python"},
        {"id": "int-b", "user_id": "test-u1", "repo_id": "test-r1", "kind": "function", "name": "bar",
         "file_path": "a.py", "start_line": 3, "end_line": 4, "language": "python"},
    ])
    neo4j_store.upsert_code_edges([{"source": "int-a", "target": "int-b", "type": "CALLS"}])

    nodes, edges = neo4j_store.get_subgraph("test-u1", "test-r1")

    assert {n["name"] for n in nodes} == {"foo", "bar"}
    assert edges == [{"source": "int-a", "target": "int-b", "type": "CALLS"}]


def test_get_subgraph_includes_mentioning_text_entities_through_real_neo4j(neo4j_store):
    neo4j_store.upsert_symbols([
        {"id": "int-s1", "user_id": "test-u1", "repo_id": "test-r1", "kind": "class", "name": "Retriever",
         "file_path": "retrieval.py", "start_line": 1, "end_line": 2, "language": "python"},
    ])
    neo4j_store.upsert_text_entities([
        {"id": "int-e1", "user_id": "test-u1", "name": "Retriever", "entity_type": "concept",
         "source_doc_id": "doc-1", "source_memory_id": None},
    ])
    neo4j_store.upsert_mentions_edges([{"source": "int-e1", "target": "int-s1"}])

    nodes, edges = neo4j_store.get_subgraph("test-u1", "test-r1")

    assert {n["id"] for n in nodes} == {"int-s1", "int-e1"}
    assert {"source": "int-e1", "target": "int-s1", "type": "MENTIONS"} in edges
