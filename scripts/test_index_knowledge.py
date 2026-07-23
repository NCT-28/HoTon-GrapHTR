#!/usr/bin/env python3
"""Sanity tests for index_knowledge.py's pure multipart-encoding helper."""
from index_knowledge import build_multipart


def test_build_multipart_contains_field_and_filename():
    body, content_type = build_multipart("file", "architecture.md", b"hello world")
    assert b'name="file"' in body
    assert b'filename="architecture.md"' in body
    assert b"hello world" in body
    assert content_type.startswith("multipart/form-data; boundary=")


def test_build_multipart_boundary_matches_body():
    body, content_type = build_multipart("file", "x.md", b"abc")
    boundary = content_type.split("boundary=")[1]
    assert body.startswith(f"--{boundary}".encode())
    assert body.rstrip().endswith(f"--{boundary}--".encode())


if __name__ == "__main__":
    test_build_multipart_contains_field_and_filename()
    test_build_multipart_boundary_matches_body()
    print("PASS")
