"""Fire-and-forget orchestration: after a document is ingested, extract text
entities from it and link them to code symbols already in the graph. Runs as
a detached asyncio task (see documents.py), so any exception here must be
caught and logged rather than raised — there's nothing upstream to catch it."""

import logging

from app.entity_extraction import extract_and_store_entities
from app.entity_linker import link_entities_to_code

logger = logging.getLogger(__name__)


async def run_entity_extraction_and_linking(
    graph_store, llm, embedder, user_id: str, source_doc_id: str, text: str
) -> None:
    try:
        stored = extract_and_store_entities(graph_store, llm, user_id, source_doc_id, text)
        if stored:
            link_entities_to_code(graph_store, llm, embedder, user_id, source_doc_id)
    except Exception:
        logger.exception("entity extraction/linking failed for document %s", source_doc_id)
