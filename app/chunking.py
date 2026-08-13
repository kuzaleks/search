import re
from dataclasses import dataclass


CHUNK_SIZE_WORDS = 400
CHUNK_OVERLAP_WORDS = 50


@dataclass(frozen=True, slots=True)
class TextChunk:
    text: str
    start_offset: int
    end_offset: int


def chunk_text(
    text: str,
    chunk_size: int = CHUNK_SIZE_WORDS,
    overlap: int = CHUNK_OVERLAP_WORDS,
) -> list[TextChunk]:
    """Split text at word boundaries, retaining context between chunks."""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap must be between zero and chunk_size")

    words = list(re.finditer(r"\S+", text))
    if not words:
        return []

    chunks = []
    step = chunk_size - overlap
    for start in range(0, len(words), step):
        end = min(start + chunk_size, len(words))
        start_offset = words[start].start()
        end_offset = words[end - 1].end()
        chunks.append(
            TextChunk(
                text=text[start_offset:end_offset],
                start_offset=start_offset,
                end_offset=end_offset,
            )
        )
        if end == len(words):
            break

    return chunks
