"""Phase 2 tests for streaming extraction APIs."""

import json
import unittest
from pathlib import Path

from extraction.extractor import (
    extract_to_dict_list,
    iter_extract_entities,
    iter_extract_to_dict_list,
)


class TestStreamingExtractionApis(unittest.TestCase):
    """Validate iterator-based extraction APIs."""

    def setUp(self) -> None:
        self.fixtures_dir = Path(__file__).parent / "fixtures"
        self.test_repo = self.fixtures_dir / "test_repo"

    def test_iter_extract_entities_matches_non_streaming_for_file(self) -> None:
        file_path = self.fixtures_dir / "simple_function.cpp"
        streaming_entities = list(iter_extract_entities(str(file_path), "stream_repo"))
        non_streaming_entities = extract_to_dict_list(str(file_path), "stream_repo")

        self.assertEqual(len(streaming_entities), len(non_streaming_entities))
        self.assertEqual(
            [entity.global_uri for entity in streaming_entities],
            [entity_dict["global_uri"] for entity_dict in non_streaming_entities],
        )

    def test_iter_extract_to_dict_list_is_json_serializable(self) -> None:
        streamed = list(iter_extract_to_dict_list(str(self.test_repo), "stream_repo"))
        self.assertGreater(len(streamed), 0)
        # Should be JSON serializable without transformation.
        encoded = json.dumps(streamed)
        self.assertIsInstance(encoded, str)

    def test_iter_extract_entities_directory_non_empty(self) -> None:
        entities = list(iter_extract_entities(str(self.test_repo), "stream_repo"))
        self.assertGreater(len(entities), 0)
        self.assertTrue(all(entity.repo_name == "stream_repo" for entity in entities))


if __name__ == "__main__":
    unittest.main()
