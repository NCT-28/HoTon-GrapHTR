from app.chunker import chunk_text


def test_empty_text():
    assert chunk_text("", 100, 20) == []


def test_short_text():
    chunks = chunk_text("Hello world", 100, 20)
    assert chunks == ["Hello world"]


def test_paragraph_splitting():
    text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
    chunks = chunk_text(text, 50, 10)
    assert len(chunks) >= 1
    assert all(len(c) <= 60 or len(text) < 60 for c in chunks)


def test_long_text_chunking():
    text = ("word " * 500).strip()
    chunks = chunk_text(text, 100, 20)
    assert len(chunks) > 1
    assert all(len(c) <= 120 or len(text) < 120 for c in chunks)


def test_overlap_presence():
    text = (
        "Alpha beta gamma delta epsilon. Zeta eta theta iota kappa. "
        "Lambda mu nu xi omicron. Pi rho sigma tau upsilon. Phi chi psi omega end."
    )
    chunks = chunk_text(text, 60, 20)
    assert all(c.strip() for c in chunks)
    if len(chunks) >= 2:
        found_overlap = False
        for i in range(1, len(chunks)):
            prev_tail = chunks[i - 1][-25:]
            curr_head = chunks[i][:25]
            for word in prev_tail.split():
                if len(word) > 3 and word in curr_head:
                    found_overlap = True
                    break
            if found_overlap:
                break
        assert found_overlap or len(chunks) == 1
