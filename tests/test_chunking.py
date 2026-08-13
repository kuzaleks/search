import unittest

from app.chunking import TextChunk, chunk_text


class ChunkTextTests(unittest.TestCase):
    def test_short_text_is_one_chunk(self) -> None:
        self.assertEqual(
            chunk_text("one two three"),
            [TextChunk("one two three", start_offset=0, end_offset=13)],
        )

    def test_chunks_overlap_at_word_boundaries(self) -> None:
        chunks = chunk_text(
            "one two three four five six seven",
            chunk_size=4,
            overlap=1,
        )

        self.assertEqual(
            chunks,
            [
                TextChunk(
                    "one two three four",
                    start_offset=0,
                    end_offset=18,
                ),
                TextChunk(
                    "four five six seven",
                    start_offset=14,
                    end_offset=33,
                ),
            ],
        )

    def test_empty_text_has_no_chunks(self) -> None:
        self.assertEqual(chunk_text("  \n\t"), [])

    def test_overlap_must_be_smaller_than_chunk(self) -> None:
        with self.assertRaises(ValueError):
            chunk_text("one two", chunk_size=2, overlap=2)


if __name__ == "__main__":
    unittest.main()
