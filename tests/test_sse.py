"""M5 tests: SSE parser for streaming responses."""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

import pytest

from app.core.sse import SSEEvent, parse_sse


async def _aiter(chunks: list[bytes]) -> AsyncIterator[bytes]:
    for c in chunks:
        yield c


async def _collect(chunks: list[bytes]) -> list[SSEEvent]:
    events = []
    async for ev in parse_sse(_aiter(chunks)):
        events.append(ev)
    return events


async def test_parse_single_complete_event():
    chunks = [b'data: {"foo": "bar"}\n\n']
    events = await _collect(chunks)
    assert len(events) == 1
    assert events[0].data == '{"foo": "bar"}'
    assert events[0].is_done is False


async def test_parse_done_sentinel():
    chunks = [b'data: [DONE]\n\n']
    events = await _collect(chunks)
    assert len(events) == 1
    assert events[0].is_done is True


async def test_parse_multiple_events_in_one_chunk():
    chunks = [
        b'data: {"a": 1}\n\n'
        b'data: {"a": 2}\n\n'
        b'data: [DONE]\n\n'
    ]
    events = await _collect(chunks)
    assert len(events) == 3
    assert events[0].data == '{"a": 1}'
    assert events[1].data == '{"a": 2}'
    assert events[2].is_done is True


async def test_parse_fragmented_across_chunks():
    """SSE event split across multiple transport reads must be reassembled."""
    chunks = [
        b'data: {"foo"',
        b': ',
        b'"bar"}\n',
        b'\n',
    ]
    events = await _collect(chunks)
    assert len(events) == 1
    assert events[0].data == '{"foo": "bar"}'


async def test_parse_byte_by_byte():
    """Each byte in its own chunk should still parse correctly."""
    text = 'data: {"x": 42}\n\n'
    chunks = [bytes([b]) for b in text.encode("utf-8")]
    events = await _collect(chunks)
    assert len(events) == 1
    assert events[0].data == '{"x": 42}'


async def test_parse_with_event_field():
    chunks = [b'event: foo\ndata: bar\n\n']
    events = await _collect(chunks)
    assert len(events) == 1
    assert events[0].data == 'bar'
    assert events[0].event == 'foo'


async def test_parse_with_id_field():
    chunks = [b'id: 42\ndata: hello\n\n']
    events = await _collect(chunks)
    assert len(events) == 1
    assert events[0].data == 'hello'
    assert events[0].id == '42'


async def test_parse_ignores_comment_lines():
    chunks = [b': this is a comment\ndata: real data\n\n']
    events = await _collect(chunks)
    assert len(events) == 1
    assert events[0].data == 'real data'


async def test_parse_ignores_blank_events():
    chunks = [b'\n\ndata: real\n\n\n\n']
    events = await _collect(chunks)
    assert len(events) == 1
    assert events[0].data == 'real'


async def test_parse_crlf_line_endings():
    chunks = [b'data: hello\r\n\r\n']
    events = await _collect(chunks)
    assert len(events) == 1
    assert events[0].data == 'hello'


async def test_parse_multiline_data():
    """A data field can span multiple lines; they are joined with \\n."""
    chunks = [b'data: line1\ndata: line2\n\n']
    events = await _collect(chunks)
    assert len(events) == 1
    assert events[0].data == 'line1\nline2'


async def test_parse_trailing_data_without_newline():
    """A trailing data line without a final blank line should still be emitted."""
    chunks = [b'data: hello']
    events = await _collect(chunks)
    assert len(events) == 1
    assert events[0].data == 'hello'


async def test_parse_empty_stream():
    events = await _collect([])
    assert events == []


async def test_parse_data_with_leading_space_stripped():
    """Per SSE spec, a single leading space after the colon is stripped."""
    chunks = [b'data: hello world\n\n']
    events = await _collect(chunks)
    assert len(events) == 1
    assert events[0].data == 'hello world'


async def test_parse_handles_unicode():
    chunks = ['data: {"text": "héllo"}\n\n'.encode("utf-8")]
    events = await _collect(chunks)
    assert len(events) == 1
    assert events[0].data == '{"text": "héllo"}'
