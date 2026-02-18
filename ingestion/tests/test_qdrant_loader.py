"""
Unit and integration tests for qdrant_loader.py
"""

import unittest
import json
import tempfile
import os
from unittest.mock import Mock, patch
from qdrant_client import models

from ingestion.qdrant_loader import (
    generate_point_id,
    build_point,
    ingest_entities,
    ingest_from_jsonl,
    get_qdrant_client,
    init_collection,
    IngestionStats,
)
from ingestion.embedding import generate_mock_embedding


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
        parts = point_id.split('-')
        self.assertEqual(len(parts), 5)
        self.assertEqual(len(parts[0]), 8)
        self.assertEqual(len(parts[1]), 4)
    
    def test_unicode_uri(self):
        """Test UUID generation with Unicode characters in URI."""
        uri = "repo::文件.cpp::Class::类名"
        
        id1 = generate_point_id(uri)
        id2 = generate_point_id(uri)
        
        # Should still be deterministic
        self.assertEqual(id1, id2)


class TestBuildPoint(unittest.TestCase):
    """Test building Qdrant points from entity dicts."""
    
    def setUp(self):
        """Set up test entity dictionary."""
        self.entity = {
            "global_uri": "test::file.cpp::Function::foo",
            "repo_name": "test",
            "file_path": "file.cpp",
            "entity_type": "Function",
            "entity_name": "foo",
            "docstring": "/// Test function",
            "code_text": "void foo() {}",
            "start_line": 1,
            "end_line": 3,
            "is_templated": False
        }
    
    def test_point_structure(self):
        """Test that PointStruct has correct structure."""
        point = build_point(self.entity, generate_mock_embedding, dimension=128)
        
        self.assertIsInstance(point, models.PointStruct)
        self.assertIsNotNone(point.id)
        self.assertIsNotNone(point.vector)
        self.assertIsNotNone(point.payload)
    
    def test_point_id_deterministic(self):
        """Test that same entity produces same point ID."""
        point1 = build_point(self.entity, generate_mock_embedding, dimension=64)
        point2 = build_point(self.entity, generate_mock_embedding, dimension=64)
        
        self.assertEqual(point1.id, point2.id)
    
    def test_vector_dimension(self):
        """Test that vector has correct dimension."""
        point = build_point(self.entity, generate_mock_embedding, dimension=256)
        
        self.assertEqual(len(point.vector), 256)
    
    def test_payload_fields(self):
        """Test that all entity fields are in payload."""
        point = build_point(self.entity, generate_mock_embedding, dimension=32)
        
        expected_keys = {
            "global_uri", "repo_name", "file_path", "entity_type",
            "entity_name", "docstring", "code_text", "start_line",
            "end_line", "is_templated"
        }
        
        self.assertEqual(set(point.payload.keys()), expected_keys)
        self.assertEqual(point.payload["global_uri"], self.entity["global_uri"])
        self.assertEqual(point.payload["entity_type"], "Function")
    
    def test_embed_text_with_docstring(self):
        """Test that embedding text includes docstring + code."""
        # Create a custom embedding function that captures the input
        captured_text = []
        
        def capture_embed(text, dim):
            captured_text.append(text)
            return [0.0] * dim
        
        point = build_point(self.entity, capture_embed, dimension=8)
        
        # Should have concatenated docstring + code
        self.assertEqual(len(captured_text), 1)
        self.assertIn("/// Test function", captured_text[0])
        self.assertIn("void foo() {}", captured_text[0])
    
    def test_embed_text_without_docstring(self):
        """Test embedding text when docstring is None."""
        entity_no_doc = self.entity.copy()
        entity_no_doc["docstring"] = None
        
        captured_text = []
        
        def capture_embed(text, dim):
            captured_text.append(text)
            return [0.0] * dim
        
        point = build_point(entity_no_doc, capture_embed, dimension=8)
        
        # Should only have code text
        self.assertEqual(len(captured_text), 1)
        self.assertEqual(captured_text[0], "void foo() {}")
    
    def test_missing_required_field(self):
        """Test that missing required field raises KeyError."""
        bad_entity = {"global_uri": "test"}  # Missing code_text
        
        with self.assertRaises(KeyError):
            build_point(bad_entity, generate_mock_embedding, dimension=8)
    
    def test_vector_dimension_mismatch(self):
        """Test that dimension mismatch raises ValueError."""
        def bad_embed(text, dim):
            # Return wrong dimension
            return [0.0] * (dim + 10)
        
        with self.assertRaises(ValueError) as ctx:
            build_point(self.entity, bad_embed, dimension=64)
        
        self.assertIn("dimension mismatch", str(ctx.exception))


class TestIngestEntities(unittest.TestCase):
    """Test entity ingestion (requires Qdrant running)."""
    
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
                "is_templated": False
            }
            for i in range(5)
        ]
    
    def test_ingest_basic(self):
        """Test basic ingestion (requires Qdrant running)."""
        try:
            client = get_qdrant_client()
        except Exception:
            self.skipTest("Qdrant not available")
        
        # Create test collection with matching dimension
        collection_name = "test_ingest_basic"
        init_collection(client, collection_name, vector_dimension=128, recreate=True)
        
        # Ingest entities
        stats = ingest_entities(
            self.entities,
            client,
            collection_name=collection_name,
            dimension=128
        )
        
        # Verify stats
        self.assertEqual(stats.points_uploaded, 5)
        self.assertEqual(stats.batches_sent, 1)
        self.assertEqual(stats.errors, 0)
        
        # Verify points in Qdrant
        count = client.count(collection_name=collection_name).count
        self.assertEqual(count, 5)
        
        # Cleanup
        client.delete_collection(collection_name)
    
    def test_ingest_idempotent(self):
        """Test that re-ingesting same data doesn't create duplicates."""
        try:
            client = get_qdrant_client()
        except Exception:
            self.skipTest("Qdrant not available")
        
        collection_name = "test_idempotent"
        init_collection(client, collection_name, vector_dimension=128, recreate=True)
        
        # Ingest once
        stats1 = ingest_entities(
            self.entities,
            client,
            collection_name=collection_name,
            dimension=128
        )
        
        count1 = client.count(collection_name=collection_name).count
        
        # Ingest again
        stats2 = ingest_entities(
            self.entities,
            client,
            collection_name=collection_name,
            dimension=128
        )
        
        count2 = client.count(collection_name=collection_name).count
        
        # Should have same count (overwrites, no duplicates)
        self.assertEqual(count1, count2)
        self.assertEqual(count1, 5)
        
        # Cleanup
        client.delete_collection(collection_name)
    
    def test_ingest_batch_size(self):
        """Test batching with small batch size."""
        try:
            client = get_qdrant_client()
        except Exception:
            self.skipTest("Qdrant not available")
        
        collection_name = "test_batch"
        init_collection(client, collection_name, vector_dimension=64, recreate=True)
        
        # Ingest with small batch size
        stats = ingest_entities(
            self.entities,
            client,
            collection_name=collection_name,
            dimension=64,
            batch_size=2  # Should create 3 batches (2+2+1)
        )
        
        self.assertEqual(stats.points_uploaded, 5)
        self.assertEqual(stats.batches_sent, 3)
        
        # Cleanup
        client.delete_collection(collection_name)
    
    def test_ingest_with_error_entity(self):
        """Test that malformed entity is skipped and logged."""
        try:
            client = get_qdrant_client()
        except Exception:
            self.skipTest("Qdrant not available")
        
        collection_name = "test_error"
        init_collection(client, collection_name, vector_dimension=64, recreate=True)
        
        # Mix valid and invalid entities
        mixed_entities = self.entities[:2] + [{"global_uri": "bad"}] + self.entities[2:3]
        
        stats = ingest_entities(
            mixed_entities,
            client,
            collection_name=collection_name,
            dimension=64
        )
        
        # Should upload valid ones, skip bad one
        self.assertEqual(stats.points_uploaded, 3)
        self.assertEqual(stats.errors, 1)
        
        # Cleanup
        client.delete_collection(collection_name)


class TestIngestFromJsonl(unittest.TestCase):
    """Test JSONL file ingestion."""
    
    def test_ingest_from_jsonl(self):
        """Test ingesting from JSONL file."""
        try:
            client = get_qdrant_client()
        except Exception:
            self.skipTest("Qdrant not available")
        
        # Create temporary JSONL file
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
                "is_templated": False
            }
            for i in range(3)
        ]
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
            for entity in entities:
                f.write(json.dumps(entity) + '\n')
            temp_path = f.name
        
        try:
            collection_name = "test_jsonl"
            init_collection(client, collection_name, vector_dimension=64, recreate=True)
            
            stats = ingest_from_jsonl(
                temp_path,
                client,
                collection_name=collection_name,
                dimension=64
            )
            
            self.assertEqual(stats.points_uploaded, 3)
            
            # Verify in Qdrant
            count = client.count(collection_name=collection_name).count
            self.assertEqual(count, 3)
            
            # Cleanup
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
        
        # Create malformed JSONL
        with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
            f.write('{"valid": "json"}\n')
            f.write('not valid json\n')  # Malformed line
            temp_path = f.name
        
        try:
            with self.assertRaises(json.JSONDecodeError):
                ingest_from_jsonl(temp_path, client)
        finally:
            os.unlink(temp_path)


class TestQdrantConnection(unittest.TestCase):
    """Test Qdrant client connection (requires Qdrant running)."""
    
    def test_get_client(self):
        """Test connecting to Qdrant."""
        try:
            client = get_qdrant_client()
            
            # Should be able to list collections
            collections = client.get_collections()
            self.assertIsNotNone(collections)
            
        except Exception as e:
            self.skipTest(f"Qdrant not available: {e}")
    
    def test_connection_retry_on_failure(self):
        """Test that connection retries on failure."""
        # Mock the QdrantClient to fail
        with patch('ingestion.qdrant_loader.QdrantClient') as mock_client:
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
        
        # Delete if exists
        if client.collection_exists(collection_name):
            client.delete_collection(collection_name)
        
        # Create
        init_collection(client, collection_name, vector_dimension=512)
        
        # Verify it exists
        self.assertTrue(client.collection_exists(collection_name))
        
        # Verify dimension
        info = client.get_collection(collection_name)
        self.assertEqual(info.config.params.vectors.size, 512)
        
        # Cleanup
        client.delete_collection(collection_name)
    
    def test_init_collection_recreate(self):
        """Test recreating an existing collection."""
        try:
            client = get_qdrant_client()
        except Exception:
            self.skipTest("Qdrant not available")
        
        collection_name = "test_recreate"
        
        # Create initial collection
        init_collection(client, collection_name, vector_dimension=128, recreate=True)
        
        # Upload a point with valid UUID
        import uuid
        test_point = models.PointStruct(
            id=str(uuid.uuid4()),
            vector=[0.0] * 128,
            payload={"test": "data"}
        )
        client.upsert(collection_name=collection_name, points=[test_point])
        
        # Verify point exists
        count1 = client.count(collection_name=collection_name).count
        self.assertEqual(count1, 1)
        
        # Recreate - should delete old data
        init_collection(client, collection_name, vector_dimension=128, recreate=True)
        
        # Should be empty
        count2 = client.count(collection_name=collection_name).count
        self.assertEqual(count2, 0)
        
        # Cleanup
        client.delete_collection(collection_name)
    
    def test_init_collection_idempotent(self):
        """Test that calling init twice without recreate is safe."""
        try:
            client = get_qdrant_client()
        except Exception:
            self.skipTest("Qdrant not available")
        
        collection_name = "test_idempotent"
        
        # Create
        init_collection(client, collection_name, vector_dimension=256, recreate=True)
        
        # Call again without recreate - should not error
        init_collection(client, collection_name, vector_dimension=256, recreate=False)
        
        # Should still exist
        self.assertTrue(client.collection_exists(collection_name))
        
        # Cleanup
        client.delete_collection(collection_name)


class TestEndToEndIntegration(unittest.TestCase):
    """End-to-end integration tests."""
    
    def test_complete_pipeline(self):
        """Test complete pipeline from extraction to Qdrant."""
        try:
            from extraction.extractor import extract_file
            client = get_qdrant_client()
        except Exception as e:
            self.skipTest(f"Dependencies not available: {e}")
        
        # Extract from test_torture.h
        entities_obj = extract_file(
            "extraction/tests/fixtures/test_torture.h",
            "integration_test"
        )
        entities = [e.to_dict() for e in entities_obj]
        
        # Create collection
        collection_name = "test_e2e"
        init_collection(client, collection_name, vector_dimension=256, recreate=True)
        
        # Ingest
        stats = ingest_entities(
            entities,
            client,
            collection_name=collection_name,
            dimension=256
        )
        
        # Verify
        self.assertGreater(stats.points_uploaded, 0)
        self.assertEqual(stats.errors, 0)
        
        count = client.count(collection_name=collection_name).count
        self.assertEqual(count, len(entities))
        
        # Verify retrieval
        scroll_result = client.scroll(
            collection_name=collection_name, 
            limit=10,
            with_vectors=True,
            with_payload=True
        )
        points = scroll_result[0]
        
        self.assertEqual(len(points), len(entities))
        
        # Verify point structure
        for point in points:
            self.assertIn("global_uri", point.payload)
            self.assertIn("code_text", point.payload)
            if point.vector is not None:
                self.assertEqual(len(point.vector), 256)
        
        # Cleanup
        client.delete_collection(collection_name)


if __name__ == "__main__":
    unittest.main()
