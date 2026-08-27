"""SSE (Server-Sent Events) parser for streaming HTTP responses (M5).

HTTP transport may split a single SSE event across multiple reads. This
parser buffers partial data and yields complete events as they become
available.

SSE format (RFC: text/event-stream):
    event: name\\n
    data: payload\\n
    \\n   (blank line terminates the event)

Blank lines that do not follow a `data:` line are ignored. Lines starting
with `:` are comments and ignored.

The parser accepts an async iterator of bytes chunks and yields parsed
SSEEvent objects. It is tolerant of:
    * fragmented reads (data split across chunks)
    * CRLF and LF line endings
    * empty/comment lines
    * multiple events in a single chunk
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator


@dataclass(frozen=True)
class SSEEvent:
    """A single parsed Server-Sent Event."""

    data: str
    event: str | None = None
    id: str | None = None

    @property
    def is_done(self) -> bool:
        """Return True if this event signals end-of-stream ([DONE])."""
        return self.data.strip() == "[DONE]"


def _parse_event_block(block: str) -> SSEEvent | None:
    """Parse a single SSE event block (one or more lines separated by \\n).

    Returns None for empty blocks or comment-only blocks.
    """
    data_parts: list[str] = []
    event_name: str | None = None
    event_id: str | None = None

    for line in block.split("\n"):
        # Strip CR if present.
        line = line.rstrip("\r")
        if not line:
            continue
        if line.startswith(":"):
            # Comment line.
            continue
        if ":" in line:
            field_name, _, value = line.partition(":")
            # SSE spec: strip a single leading space from value.
            if value.startswith(" "):
                value = value[1:]
        else:
            field_name = line
            value = ""

        if field_name == "data":
            data_parts.append(value)
        elif field_name == "event":
            event_name = value
        elif field_name == "id":
            event_id = value
        # Other fields (retry, etc.) are ignored for our use case.

    if not data_parts and event_name is None:
        return None

    return SSEEvent(data="\n".join(data_parts), event=event_name, id=event_id)


async def parse_sse(
    chunks: AsyncIterator[bytes],
) -> AsyncIterator[SSEEvent]:
    """Parse an async stream of bytes into SSE events.

    Buffers across chunk boundaries so that a single SSE event split across
    transport reads is reassembled correctly.
    """
    buffer = ""
    async for chunk in chunks:
        if not chunk:
            continue
        buffer += chunk.decode("utf-8", errors="replace")
        # SSE events are separated by a blank line (\\n\\n).
        # We split on \\n\\n, but must keep any trailing partial event
        # in the buffer for the next chunk.
        while "\n\n" in buffer:
            block, buffer = buffer.split("\n\n", 1)
            event = _parse_event_block(block)
            if event is not None:
                yield event
    # Flush any remaining buffer at end of stream.
    if buffer.strip():
        event = _parse_event_block(buffer)
        if event is not None:
            yield event
