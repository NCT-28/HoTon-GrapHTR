"""Recursive character text splitter — ported from hoton-lmr/src/rag/chunker.rs."""

DEFAULT_CHUNK_SIZE = 1800
DEFAULT_OVERLAP = 180

_SEPARATORS = ["\n\n", "\n", ". ", " "]


def chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    if not text:
        return []

    chunks: list[str] = []
    _chunk_recursive(text, chunk_size, overlap, _SEPARATORS, chunks)

    return [c for c in chunks if c.strip()]


def _chunk_recursive(
    text: str,
    chunk_size: int,
    overlap: int,
    separators: list[str],
    chunks: list[str],
) -> None:
    if len(text) <= chunk_size:
        chunks.append(text)
        return

    if not separators:
        split_at = min(chunk_size, len(text))
        if split_at > 0:
            chunks.append(text[:split_at])
        remaining = text[split_at:]
        if remaining:
            _chunk_recursive(remaining, chunk_size, overlap, separators, chunks)
        return

    sep = separators[0]
    rest_seps = separators[1:]

    split_pos = _find_split_point(text, sep, chunk_size)
    if split_pos is not None:
        chunk = text[:split_pos].rstrip()
        remaining = text[split_pos:].lstrip()

        if chunk:
            chunks.append(chunk)

        if overlap > 0 and remaining:
            overlap_text = _get_overlap_text(text, split_pos, overlap)
            _chunk_recursive(overlap_text + remaining, chunk_size, overlap, separators, chunks)
        elif remaining:
            _chunk_recursive(remaining, chunk_size, overlap, separators, chunks)
    else:
        _chunk_recursive(text, chunk_size, overlap, rest_seps, chunks)


def _find_split_point(text: str, sep: str, chunk_size: int) -> int | None:
    search_end = min(max(chunk_size - len(sep), 0), max(len(text) - len(sep), 0))
    idx = text.rfind(sep, 0, search_end + len(sep))
    if idx == -1 or idx > search_end:
        return None
    return idx + len(sep)


def _get_overlap_text(text: str, split_pos: int, overlap: int) -> str:
    start = max(split_pos - overlap, 0)
    return text[start:split_pos].lstrip()
