"""
Unit and integration tests for qdrant_loader.py

Tests cover:
- IngestionStats dataclass
- Deterministic UUIDv5 point-ID generation
- build_point (now accepts a pre-computed vector)
- Batch ingestion with embed_fn called once per batch
- JSONL ingestion
- Qdrant connection and collection initialization
- End-to-end integration pipeline
"""

import unittest
import json
import tempfile
import os
from unittest.mock import Mock, patch, call
from typing import List

import pytest
from qdrant_client import models

from ingestion.qdrant_loader import (
    generate_point_id,
    build_point,
    _build_embed_text,
    ingest_entities,
    ingest_from_jsonl,
    iter_jsonl_entity_batches,
    get_qdrant_client,
    init_collection,
    IngestionStats,
)
from ingestion.embedding import generate_mock_embedding, generate_mock_embeddings


class TestIngestionStats(unittest.TestCase):
    """Test IngestionStats dataclass."""

    def test_creation(self):
        """Test creating stats object."""
        stats = IngestionStats()
        self.assertEqual(stats.points_uploaded, 0)
        self.assertEqual(stats.batches_sent, 0)
        self.assertEqual(stats.errors, 0)

    def test_str_representation(self):
        """Test string representation."""
        stats = IngestionStats(points_uploaded=10, batches_sent=2, errors=1)
        s = str(stats)
        self.assertIn("points=10", s)
        self.assertIn("batches=2", s)
        self.assertIn("errors=1", s)

    def test_slo_report(self):
        """Test SLO-style ingestion report payload."""
        stats = IngestionStats(
            points_uploaded=8,
            points_attempted=10,
            batches_sent=2,
            batches_failed=1,
            retry_attempts=3,
            errors=2,
        )
        stats.add_drop("embedding_failure", 2)
        report = stats.to_slo_report()
        self.assertEqual(report["points_attempted"], 10)
        self.assertEqual(report["points_uploaded"], 8)
        self.assertEqual(report["points_failed"], 2)
        self.assertEqual(report["retry_attempts"], 3)
        self.assertAlmostEqual(report["success_rate"], 0.8, places=6)
        self.assertEqual(report["dropped_by_reason"]["embedding_failure"], 2)


class TestGeneratePointId(unittest.TestCase):
    """Test UUIDv5 point ID generation."""

    def test_deterministic(self):
        """Test that same URI produces same UUID."""
        uri = "repo::file.cpp::Class::Foo"

        id1 = generate_point_id(uri)
        id2 = generate_point_id(uri)

        self.assertEqual(id1, id2)

    def test_unique(self):
        """Test that different URIs produce different UUIDs."""
        id1 = generate_point_id("repo::file1.cpp::Class::Foo")
        id2 = generate_point_id("repo::file2.cpp::Class::Bar")

        self.assertNotEqual(id1, id2)

    def test_uuid_format(self):
        """Test that output is a valid UUID string."""
        uri = "test::test.cpp::Function::test"
        point_id = generate_point_id(uri)

        # Should be a string
        self.assertIsInstance(point_id, str)

        # Should have UUID format (8-4-4-4-12)
        parts = point_id.split("-")
        self.assertEqual(len(parts), 5)
        self.assertEqual(len(parts[0]), 8)
        self.assertEqual(len(parts[1]), 4)

    def test_unicode_uri(self):
        """Test UUID generation with Unicode characters in URI."""
        uri = "repo::\u6587\u4ef6.cpp::Class::\u7c7b\u540d"

        id1 = generate_point_id(uri)
        id2 = generate_point_id(uri)

        # Should still be deterministic
        self.assertEqual(id1, id2)

    def test_function_sig_hash_changes_point_id(self):
        """Same join URI with different function signatures must not collide."""
        uri = "repo::math.cpp::Function::add"
        id1 = generate_point_id(uri, function_sig_hash="sig_aaaaaaaa")
        id2 = generate_point_id(uri, function_sig_hash="sig_bbbbbbbb")
        self.assertNotEqual(id1, id2)


class TestBuildEmbedText(unittest.TestCase):
    """Test the _build_embed_text helper."""

    def test_with_docstring(self):
        """Test text construction when docstring is present."""
        entity = {"docstring": "/// A function", "code_text": "void foo() {}"}
        result = _build_embed_text(entity)
        self.assertEqual(result, "/// A function\nvoid foo() {}")

    def test_without_docstring(self):
        """Test text construction when docstring is None."""
        entity = {"docstring": None, "code_text": "void foo() {}"}
        result = _build_embed_text(entity)
        self.assertEqual(result, "void foo() {}")

    def test_missing_docstring_key(self):
        """Test text construction when docstring key is absent."""
        entity = {"code_text": "void foo() {}"}
        result = _build_embed_text(entity)
        self.assertEqual(result, "void foo() {}")

    def test_missing_code_text_raises(self):
        """Test that missing code_text raises KeyError."""
        entity = {"docstring": "/// comment"}
        with self.assertRaises(KeyError):
            _build_embed_text(entity)


class TestBuildPoint(unittest.TestCase):
    """Test building Qdrant points from entity dicts + pre-computed vectors."""

    def setUp(self):
        """Set up test entity dictionary."""
        self.entity = {
            "global_uri": "test::file.cpp::Function::foo",
            "function_sig_hash": "sig_abc12345",
            "repo_name": "test",
            "file_path": "file.cpp",
            "entity_type": "Function",
            "entity_name": "foo",
            "docstring": "/// Test function",
            "code_text": "void foo() {}",
            "start_line": 1,
            "end_line": 3,
            "is_templated": False,
        }
        self.vector = [0.1] * 128

    def test_point_structure(self):
        """Test that PointStruct has correct structure."""
        point = build_point(self.entity, self.vector)

        self.assertIsInstance(point, models.PointStruct)
        self.assertIsNotNone(point.id)
        self.assertIsNotNone(point.vector)
        self.assertIsNotNone(point.payload)

    def test_point_id_deterministic(self):
        """Test that same entity produces same point ID."""
        point1 = build_point(self.entity, self.vector)
        point2 = build_point(self.entity, self.vector)

        self.assertEqual(point1.id, point2.id)

    def test_vector_stored_as_is(self):
        """Test that the pre-computed vector is stored without modification."""
        vec = [0.5, -0.3, 0.9, 0.0]
        point = build_point(self.entity, vec)

        self.assertEqual(point.vector, vec)

    def test_payload_fields(self):
        """Test that all entity fields are in payload."""
        point = build_point(self.entity, self.vector)

        expected_keys = {
            "global_uri",
            "identity_key",
            "function_sig_hash",
            "repo_name",
            "file_path",
            "entity_type",
            "entity_name",
            "docstring",
            "code_text",
            "start_line",
            "end_line",
            "is_templated",
        }

        self.assertEqual(set(point.payload.keys()), expected_keys)
        self.assertEqual(point.payload["global_uri"], self.entity["global_uri"])
        self.assertEqual(
            point.payload["identity_key"],
            "test::file.cpp::Function::foo::sig_abc12345",
        )
        self.assertEqual(point.payload["entity_type"], "Function")

    def test_missing_required_field(self):
        """Test that missing required field raises KeyError."""
        bad_entity = {"global_uri": "test"}  # Missing many fields

        with self.assertRaises(KeyError):
            build_point(bad_entity, self.vector)


class TestIngestEntities(unittest.TestCase):
    """Test entity ingestion with batch embedding."""

    def setUp(self):
        """Set up test entities."""
        self.entities = [
            {
                "global_uri": f"test::test.cpp::Function::func{i}",
                "repo_name": "test",
                "file_path": "test.cpp",
                "entity_type": "Function",
                "entity_name": f"func{i}",
                "docstring": f"/// Function {i}",
                "code_text": f"void func{i}() {{}}",
                "start_line": i,
                "end_line": i + 2,
                "is_templated": False,
            }
            for i in range(5)
        ]

    def test_embed_fn_called_once_per_batch(self):
        """Test that embed_fn is called exactly once per batch."""
        mock_embed = Mock(
            side_effect=lambda texts, dim: [[0.0] * dim for _ in texts]
        )
        mock_client = Mock()

        stats = ingest_entities(
            self.entities,
            mock_client,
            embed_fn=mock_embed,
            dimension=64,
            batch_size=100,  # All 5 in one batch
        )

        # Should be called exactly once (one batch)
        mock_embed.assert_called_once()

        # First arg should be a list of 5 strings
        call_args = mock_embed.call_args
        self.assertEqual(len(call_args[0][0]), 5)
        self.assertEqual(call_args[0][1], 64)

    def test_embed_fn_called_per_batch_with_small_batches(self):
        """Test that embed_fn is called once per batch chunk."""
        mock_embed = Mock(
            side_effect=lambda texts, dim: [[0.0] * dim for _ in texts]
        )
        mock_client = Mock()

        stats = ingest_entities(
            self.entities,
            mock_client,
            embed_fn=mock_embed,
            dimension=64,
            batch_size=2,  # 3 batches: 2+2+1
        )

        self.assertEqual(mock_embed.call_count, 3)
        self.assertEqual(stats.batches_sent, 3)
        self.assertEqual(stats.points_uploaded, 5)

    def test_ingest_basic(self):
        """Test basic ingestion (requires Qdrant running)."""
        try:
            client = get_qdrant_client()
        except Exception:
            self.skipTest("Qdrant not available")

        collection_name = "test_ingest_basic"
        init_collection(client, collection_name, vector_dimension=128, recreate=True)

        stats = ingest_entities(
            self.entities,
            client,
            collection_name=collection_name,
            dimension=128,
        )

        self.assertEqual(stats.points_uploaded, 5)
        self.assertEqual(stats.batches_sent, 1)
        self.assertEqual(stats.errors, 0)

        count = client.count(collection_name=collection_name).count
        self.assertEqual(count, 5)

        client.delete_collection(collection_name)

    test_ingest_basic = pytest.mark.integration(test_ingest_basic)

    def test_ingest_idempotent(self):
        """Test that re-ingesting same data doesn't create duplicates."""
        try:
            client = get_qdrant_client()
        except Exception:
            self.skipTest("Qdrant not available")

        collection_name = "test_idempotent"
        init_collection(client, collection_name, vector_dimension=128, recreate=True)

        stats1 = ingest_entities(
            self.entities,
            client,
            collection_name=collection_name,
            dimension=128,
        )
        count1 = client.count(collection_name=collection_name).count

        stats2 = ingest_entities(
            self.entities,
            client,
            collection_name=collection_name,
            dimension=128,
        )
        count2 = client.count(collection_name=collection_name).count

        self.assertEqual(count1, count2)
        self.assertEqual(count1, 5)

        client.delete_collection(collection_name)

    test_ingest_idempotent = pytest.mark.integration(test_ingest_idempotent)

    def test_ingest_batch_size(self):
        """Test batching with small batch size."""
        try:
            client = get_qdrant_client()
        except Exception:
            self.skipTest("Qdrant not available")

        collection_name = "test_batch"
        init_collection(client, collection_name, vector_dimension=64, recreate=True)

        stats = ingest_entities(
            self.entities,
            client,
            collection_name=collection_name,
            dimension=64,
            batch_size=2,  # Should create 3 batches (2+2+1)
        )

        self.assertEqual(stats.points_uploaded, 5)
        self.assertEqual(stats.batches_sent, 3)

        client.delete_collection(collection_name)

    test_ingest_batch_size = pytest.mark.integration(test_ingest_batch_size)

    def test_ingest_with_error_entity(self):
        """Test that malformed entity is skipped and logged."""
        try:
            client = get_qdrant_client()
        except Exception:
            self.skipTest("Qdrant not available")

        collection_name = "test_error"
        init_collection(client, collection_name, vector_dimension=64, recreate=True)

        # Mix valid and invalid entities (missing code_text)
        mixed_entities = (
            self.entities[:2] + [{"global_uri": "bad"}] + self.entities[2:3]
        )

        stats = ingest_entities(
            mixed_entities,
            client,
            collection_name=collection_name,
            dimension=64,
        )

        # Should upload valid ones, skip bad one
        self.assertEqual(stats.points_uploaded, 3)
        self.assertEqual(stats.errors, 1)

        client.delete_collection(collection_name)

    test_ingest_with_error_entity = pytest.mark.integration(test_ingest_with_error_entity)

    def test_ingest_embed_fn_failure_skips_batch(self):
        """Test that if embed_fn raises, the entire batch is skipped."""
        mock_embed = Mock(side_effect=RuntimeError("API down"))
        mock_client = Mock()

        stats = ingest_entities(
            self.entities,
            mock_client,
            embed_fn=mock_embed,
            dimension=64,
            batch_size=100,
        )

        self.assertEqual(stats.points_uploaded, 0)
        self.assertEqual(stats.errors, 5)

    def test_embed_texts_contain_docstring_and_code(self):
        """Test that the texts sent to embed_fn include docstring + code."""
        captured_texts: List[List[str]] = []

        def capture_embed(texts: List[str], dim: int) -> List[List[float]]:
            captured_texts.append(texts)
            return [[0.0] * dim for _ in texts]

        mock_client = Mock()

        ingest_entities(
            self.entities[:1],
            mock_client,
            embed_fn=capture_embed,
            dimension=64,
        )

        self.assertEqual(len(captured_texts), 1)
        self.assertIn("/// Function 0", captured_texts[0][0])
        self.assertIn("void func0() {}", captured_texts[0][0])

    def test_embedding_batches_split_by_char_budget(self):
        """Test batch splitting by character budget even with large batch_size."""
        long_entities = [
            {
                "global_uri": f"test::test.cpp::Function::f{i}",
                "repo_name": "test",
                "file_path": "test.cpp",
                "entity_type": "Function",
                "entity_name": f"f{i}",
                "docstring": None,
                "code_text": "x" * 60,
                "start_line": i,
                "end_line": i + 1,
                "is_templated": False,
            }
            for i in range(3)
        ]

        mock_embed = Mock(side_effect=lambda texts, dim: [[0.0] * dim for _ in texts])
        mock_client = Mock()

        stats = ingest_entities(
            long_entities,
            mock_client,
            embed_fn=mock_embed,
            dimension=16,
            batch_size=100,  # would normally be one batch
            max_embed_chars_per_batch=100,  # force split
        )

        # 60 + 60 exceeds 100, so 3 entities become 3 batches.
        self.assertEqual(mock_embed.call_count, 3)
        self.assertEqual(stats.batches_sent, 3)
        self.assertEqual(stats.points_uploaded, 3)

    def test_upsert_retries_then_succeeds(self):
        """Test that transient upsert failures are retried."""
        mock_embed = Mock(side_effect=lambda texts, dim: [[0.0] * dim for _ in texts])
        mock_client = Mock()
        mock_client.upsert.side_effect = [RuntimeError("temporary"), None]

        stats = ingest_entities(
            self.entities[:1],
            mock_client,
            embed_fn=mock_embed,
            dimension=16,
            batch_size=10,
            upsert_retries=2,
            upsert_retry_base_delay=0.0,
        )

        self.assertEqual(mock_client.upsert.call_count, 2)
        self.assertEqual(stats.points_uploaded, 1)
        self.assertEqual(stats.errors, 0)


class TestIngestFromJsonl(unittest.TestCase):
    """Test JSONL file ingestion."""

    def test_ingest_from_jsonl(self):
        """Test ingesting from JSONL file."""
        try:
            client = get_qdrant_client()
        except Exception:
            self.skipTest("Qdrant not available")

        entities = [
            {
                "global_uri": f"test::test.cpp::Function::func{i}",
                "repo_name": "test",
                "file_path": "test.cpp",
                "entity_type": "Function",
                "entity_name": f"func{i}",
                "docstring": None,
                "code_text": f"void func{i}() {{}}",
                "start_line": i,
                "end_line": i + 1,
                "is_templated": False,
            }
            for i in range(3)
        ]

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False
        ) as f:
            for entity in entities:
                f.write(json.dumps(entity) + "\n")
            temp_path = f.name

        try:
            collection_name = "test_jsonl"
            init_collection(
                client, collection_name, vector_dimension=64, recreate=True
            )

            stats = ingest_from_jsonl(
                temp_path,
                client,
                collection_name=collection_name,
                dimension=64,
            )

            self.assertEqual(stats.points_uploaded, 3)

            count = client.count(collection_name=collection_name).count
            self.assertEqual(count, 3)

            client.delete_collection(collection_name)

        finally:
            os.unlink(temp_path)

    def test_jsonl_nonexistent_file(self):
        """Test that nonexistent file raises FileNotFoundError."""
        try:
            client = get_qdrant_client()
        except Exception:
            self.skipTest("Qdrant not available")

        with self.assertRaises(FileNotFoundError):
            ingest_from_jsonl("/nonexistent/file.jsonl", client)

    def test_jsonl_malformed(self):
        """Test that malformed JSONL raises JSONDecodeError."""
        try:
            client = get_qdrant_client()
        except Exception:
            self.skipTest("Qdrant not available")

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False
        ) as f:
            f.write('{"valid": "json"}\n')
            f.write("not valid json\n")
            temp_path = f.name

        try:
            with self.assertRaises(json.JSONDecodeError):
                ingest_from_jsonl(temp_path, client)
        finally:
            os.unlink(temp_path)

    test_ingest_from_jsonl = pytest.mark.integration(test_ingest_from_jsonl)
    test_jsonl_nonexistent_file = pytest.mark.integration(test_jsonl_nonexistent_file)
    test_jsonl_malformed = pytest.mark.integration(test_jsonl_malformed)


class TestJsonlStreamingIterator(unittest.TestCase):
    """Test streaming JSONL batch iterator utility."""

    def test_iter_jsonl_entity_batches_chunks_by_size(self):
        entities = [{"global_uri": f"u{i}", "code_text": "x"} for i in range(5)]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            for entity in entities:
                f.write(json.dumps(entity) + "\n")
            temp_path = f.name

        try:
            batches = list(iter_jsonl_entity_batches(temp_path, batch_size=2))
            self.assertEqual(len(batches), 3)
            self.assertEqual(len(batches[0]), 2)
            self.assertEqual(len(batches[1]), 2)
            self.assertEqual(len(batches[2]), 1)
        finally:
            os.unlink(temp_path)

    def test_iter_jsonl_entity_batches_malformed_raises(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write('{"global_uri":"ok","code_text":"x"}\n')
            f.write("{bad}\n")
            temp_path = f.name

        try:
            with self.assertRaises(json.JSONDecodeError):
                list(iter_jsonl_entity_batches(temp_path, batch_size=10))
        finally:
            os.unlink(temp_path)


class TestQdrantConnection(unittest.TestCase):
    """Test Qdrant client connection (requires Qdrant running)."""

    def test_get_client(self):
        """Test connecting to Qdrant."""
        try:
            client = get_qdrant_client()

            collections = client.get_collections()
            self.assertIsNotNone(collections)

        except Exception as e:
            self.skipTest(f"Qdrant not available: {e}")
    test_get_client = pytest.mark.integration(test_get_client)

    def test_connection_retry_on_failure(self):
        """Test that connection retries on failure."""
        with patch("ingestion.qdrant_loader.QdrantClient") as mock_client:
            mock_client.side_effect = Exception("Connection failed")

            with self.assertRaises(ConnectionError) as ctx:
                get_qdrant_client()

            self.assertIn("Failed to connect", str(ctx.exception))


class TestInitCollection(unittest.TestCase):
    """Test collection initialization."""

    def test_init_collection_create(self):
        """Test creating a new collection."""
        try:
            client = get_qdrant_client()
        except Exception:
            self.skipTest("Qdrant not available")

        collection_name = "test_create"

        if client.collection_exists(collection_name):
            client.delete_collection(collection_name)

        init_collection(client, collection_name, vector_dimension=512)

        self.assertTrue(client.collection_exists(collection_name))

        info = client.get_collection(collection_name)
        self.assertEqual(info.config.params.vectors.size, 512)

        client.delete_collection(collection_name)
    test_init_collection_create = pytest.mark.integration(test_init_collection_create)

    def test_init_collection_recreate(self):
        """Test recreating an existing collection."""
        try:
            client = get_qdrant_client()
        except Exception:
            self.skipTest("Qdrant not available")

        collection_name = "test_recreate"

        init_collection(
            client, collection_name, vector_dimension=128, recreate=True
        )

        import uuid

        test_point = models.PointStruct(
            id=str(uuid.uuid4()), vector=[0.0] * 128, payload={"test": "data"}
        )
        client.upsert(collection_name=collection_name, points=[test_point])

        count1 = client.count(collection_name=collection_name).count
        self.assertEqual(count1, 1)

        init_collection(
            client, collection_name, vector_dimension=128, recreate=True
        )

        count2 = client.count(collection_name=collection_name).count
        self.assertEqual(count2, 0)

        client.delete_collection(collection_name)
    test_init_collection_recreate = pytest.mark.integration(test_init_collection_recreate)

    def test_init_collection_idempotent(self):
        """Test that calling init twice without recreate is safe."""
        try:
            client = get_qdrant_client()
        except Exception:
            self.skipTest("Qdrant not available")

        collection_name = "test_idempotent"

        init_collection(
            client, collection_name, vector_dimension=256, recreate=True
        )

        # Call again without recreate — should not error
        init_collection(
            client, collection_name, vector_dimension=256, recreate=False
        )

        self.assertTrue(client.collection_exists(collection_name))

        client.delete_collection(collection_name)
    test_init_collection_idempotent = pytest.mark.integration(test_init_collection_idempotent)

    def test_init_collection_dimension_mismatch_raises(self):
        """Test that existing collection dimension mismatch raises ValueError."""
        mock_client = Mock()
        mock_client.collection_exists.return_value = True

        # Build nested mock matching get_collection().config.params.vectors.size
        vectors = Mock()
        vectors.size = 128
        params = Mock()
        params.vectors = vectors
        config = Mock()
        config.params = params
        collection_info = Mock()
        collection_info.config = config
        mock_client.get_collection.return_value = collection_info

        with self.assertRaises(ValueError):
            init_collection(
                mock_client,
                collection_name="existing_collection",
                vector_dimension=256,
                recreate=False,
            )


class TestEndToEndIntegration(unittest.TestCase):
    """End-to-end integration tests."""

    def test_complete_pipeline(self):
        """Test complete pipeline from extraction to Qdrant."""
        try:
            from extraction.extractor import extract_file

            client = get_qdrant_client()
        except Exception as e:
            self.skipTest(f"Dependencies not available: {e}")

        entities_obj = extract_file(
            "extraction/tests/fixtures/test_torture.h", "integration_test"
        )
        entities = [e.to_dict() for e in entities_obj]

        collection_name = "test_e2e"
        init_collection(
            client, collection_name, vector_dimension=256, recreate=True
        )

        stats = ingest_entities(
            entities,
            client,
            collection_name=collection_name,
            dimension=256,
        )

        self.assertGreater(stats.points_uploaded, 0)
        self.assertEqual(stats.errors, 0)

        count = client.count(collection_name=collection_name).count
        self.assertEqual(count, len(entities))

        scroll_result = client.scroll(
            collection_name=collection_name,
            limit=10,
            with_vectors=True,
            with_payload=True,
        )
        points = scroll_result[0]

        self.assertEqual(len(points), len(entities))

        for point in points:
            self.assertIn("global_uri", point.payload)
            self.assertIn("code_text", point.payload)
            if point.vector is not None:
                self.assertEqual(len(point.vector), 256)

        client.delete_collection(collection_name)
    test_complete_pipeline = pytest.mark.integration(test_complete_pipeline)


if __name__ == "__main__":
    unittest.main()
