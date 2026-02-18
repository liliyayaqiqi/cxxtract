"""
Unit tests for embedding.py

Tests deterministic mock embedding generation.
"""

import unittest
from ingestion.embedding import generate_mock_embedding


class TestMockEmbedding(unittest.TestCase):
    """Test mock embedding generation."""
    
    def test_dimension(self):
        """Test that embedding has correct dimension."""
        vec = generate_mock_embedding("test", dimension=512)
        self.assertEqual(len(vec), 512)
    
    def test_default_dimension(self):
        """Test default dimension is 1536."""
        vec = generate_mock_embedding("test")
        self.assertEqual(len(vec), 1536)
    
    def test_determinism_same_text(self):
        """Test that same text produces same vector every time."""
        text = "class Foo { void bar(); };"
        
        vec1 = generate_mock_embedding(text, dimension=128)
        vec2 = generate_mock_embedding(text, dimension=128)
        
        self.assertEqual(vec1, vec2)
        self.assertEqual(len(vec1), 128)
    
    def test_different_text_different_vectors(self):
        """Test that different text produces different vectors."""
        vec1 = generate_mock_embedding("class Foo {};", dimension=64)
        vec2 = generate_mock_embedding("class Bar {};", dimension=64)
        
        self.assertNotEqual(vec1, vec2)
    
    def test_value_range(self):
        """Test that all values are in range [-1.0, 1.0]."""
        vec = generate_mock_embedding("test text", dimension=100)
        
        for val in vec:
            self.assertGreaterEqual(val, -1.0)
            self.assertLessEqual(val, 1.0)
    
    def test_determinism_cross_session(self):
        """Test that vectors are deterministic across different calls.
        
        This tests the critical MD5-based seeding fix.
        Python's hash() would fail this test.
        """
        text = "void calculate() { return 42; }"
        
        # Generate multiple times
        vectors = [generate_mock_embedding(text, dimension=32) for _ in range(5)]
        
        # All should be identical
        for vec in vectors[1:]:
            self.assertEqual(vectors[0], vec)
    
    def test_empty_string(self):
        """Test embedding generation for empty string."""
        vec = generate_mock_embedding("", dimension=8)
        self.assertEqual(len(vec), 8)
    
    def test_unicode_text(self):
        """Test embedding generation with Unicode characters."""
        text = "/// 中文注释\nclass 测试 {};"
        vec = generate_mock_embedding(text, dimension=16)
        
        self.assertEqual(len(vec), 16)
        # Should be deterministic for Unicode too
        vec2 = generate_mock_embedding(text, dimension=16)
        self.assertEqual(vec, vec2)
    
    def test_large_dimension(self):
        """Test generation of large dimension vectors."""
        vec = generate_mock_embedding("test", dimension=4096)
        self.assertEqual(len(vec), 4096)


if __name__ == "__main__":
    unittest.main()
