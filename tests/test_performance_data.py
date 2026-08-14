import argparse
import unittest

from pydantic import EmailStr, TypeAdapter

from app.config import EMBEDDING_DIMENSIONS
from app.chunking import chunk_text
from scripts.performance_data import (
    TOPICS,
    make_document_content,
    make_embedding,
    performance_email,
    validate_run_id,
)


class PerformanceDataTests(unittest.TestCase):
    def test_generated_document_has_requested_ascii_byte_size(self) -> None:
        content = make_document_content(TOPICS[0], 1, 2, 10_000)
        other_content = make_document_content(TOPICS[0], 2, 2, 10_000)

        self.assertEqual(len(content.encode("ascii")), 10_000)
        self.assertIn("Retirement planning", content)
        self.assertEqual(len(chunk_text(content)), 5)
        self.assertNotEqual(content, other_content)

    def test_embeddings_have_expected_dimensions_and_topic_cluster(self) -> None:
        first = make_embedding(0, 1, 2, 0)
        second = make_embedding(1, 1, 2, 0)

        self.assertEqual(len(first), EMBEDDING_DIMENSIONS)
        self.assertEqual(first[0], 1.0)
        self.assertEqual(second[1], 1.0)
        self.assertNotEqual(first, second)

    def test_performance_email_identifies_run(self) -> None:
        email = performance_email("baseline10k", 42)

        self.assertEqual(
            email,
            "perf+baseline10k.00042@performance-test.nevis.dev",
        )
        self.assertEqual(TypeAdapter(EmailStr).validate_python(email), email)

    def test_run_id_rejects_sql_wildcards(self) -> None:
        with self.assertRaises(argparse.ArgumentTypeError):
            validate_run_id("run_%")


if __name__ == "__main__":
    unittest.main()
