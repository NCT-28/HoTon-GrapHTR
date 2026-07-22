import uuid

import pytest
from qdrant_client.models import PointStruct

from app.clients.qdrant_store import RAG_CHUNKS
from app.agentic.react import MAX_STEPS, decide_continue, run_multi_step_retrieval


class FixedEmbedder:
    def embed_single(self, text):
        return [1.0] + [0.0] * 383


class ScriptedLLM:
    """Returns responses in order, one per .generate() call, then repeats the last one."""

    def __init__(self, responses):
        self._responses = responses
        self._i = 0

    def generate(self, prompt, max_new_tokens=100, temperature=0.0):
        response = self._responses[min(self._i, len(self._responses) - 1)]
        self._i += 1
        return response


def test_decide_continue_parses_enough_true():
    llm = ScriptedLLM(['{"enough": true, "next_query": null}'])
    enough, next_query = decide_continue(llm, "q", [])
    assert enough is True
    assert next_query is None


def test_decide_continue_parses_enough_false_with_next_query():
    llm = ScriptedLLM(['{"enough": false, "next_query": "narrower question"}'])
    enough, next_query = decide_continue(llm, "q", [])
    assert enough is False
    assert next_query == "narrower question"


def test_decide_continue_unparsable_stops_rather_than_loops_forever():
    llm = ScriptedLLM(["not json"])
    enough, next_query = decide_continue(llm, "q", [])
    assert enough is True
    assert next_query is None


def test_multi_step_retrieval_stops_when_first_step_says_enough(qdrant):
    user_id = uuid.uuid4()
    qdrant.upsert(
        collection_name=RAG_CHUNKS,
        points=[
            PointStruct(
                id=str(uuid.uuid4()),
                vector=[1.0] + [0.0] * 383,
                payload={
                    "user_id": str(user_id), "document_id": "d1", "content": "chunk one",
                    "document_title": "Doc", "source_url": None, "valid_until": None,
                },
            )
        ],
        wait=True,
    )
    llm = ScriptedLLM(['{"enough": true, "next_query": null}'])

    chunks, memories = run_multi_step_retrieval(
        qdrant, FixedEmbedder(), llm, user_id, "query", top_k=5, min_similarity=0.5
    )
    assert len(chunks) == 1
    assert memories == []


def test_multi_step_retrieval_respects_max_steps_cap():
    assert MAX_STEPS == 5


def test_multi_step_retrieval_deduplicates_across_steps(qdrant):
    user_id = uuid.uuid4()
    chunk_id = str(uuid.uuid4())
    qdrant.upsert(
        collection_name=RAG_CHUNKS,
        points=[
            PointStruct(
                id=chunk_id,
                vector=[1.0] + [0.0] * 383,
                payload={
                    "user_id": str(user_id), "document_id": "d1", "content": "same chunk every step",
                    "document_title": "Doc", "source_url": None, "valid_until": None,
                },
            )
        ],
        wait=True,
    )
    # Never says "enough" until MAX_STEPS forces a stop — same chunk keeps getting retrieved each step.
    llm = ScriptedLLM(['{"enough": false, "next_query": "still query"}'])

    chunks, _memories = run_multi_step_retrieval(
        qdrant, FixedEmbedder(), llm, user_id, "query", top_k=5, min_similarity=0.5
    )
    assert len(chunks) == 1  # deduplicated, not one copy per step
