import unittest
from unittest.mock import Mock

from scripts.benchmark_search import (
    CASES,
    percentile,
    validate_response,
)


class BenchmarkSearchTests(unittest.TestCase):
    def test_percentile_uses_nearest_rank(self) -> None:
        self.assertEqual(percentile([10, 20, 30, 40], 0.50), 20)
        self.assertEqual(percentile([10, 20, 30, 40], 0.95), 40)

    def test_expected_client_is_required_for_valid_result(self) -> None:
        case = CASES[0]
        response = Mock(
            status_code=200,
            json=Mock(
                return_value={
                    "query": case.query,
                    "clients": [],
                    "documents": [],
                }
            ),
        )

        error = validate_response(case, response)

        self.assertIn("expected client not found", error)

    def test_valid_response_passes_correctness_check(self) -> None:
        case = CASES[2]
        response = Mock(
            status_code=200,
            json=Mock(
                return_value={
                    "query": case.query,
                    "clients": [],
                    "documents": [
                        {
                            "document": {
                                "title": case.expected_document_title
                            }
                        }
                    ],
                }
            ),
        )

        self.assertIsNone(validate_response(case, response))


if __name__ == "__main__":
    unittest.main()
