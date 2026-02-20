"""
Unit tests for embedding.py

Tests deterministic mock embedding generation (single + batch),
text truncation, and the get_embeddings router.
"""

import unittest
from unittest.mock import patch, MagicMock

from ingestion.embedding import (
    generate_mock_embedding,
    generate_mock_embeddings,
    get_embeddings,
    truncate_text,
)


class TestMockEmbedding(unittest.TestCase):
    """Test single-text mock embedding generation (backward compat)."""

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
        text = "/// \u4e2d\u6587\u6ce8\u91ca\nclass \u6d4b\u8bd5 {};"
        vec = generate_mock_embedding(text, dimension=16)

        self.assertEqual(len(vec), 16)
        # Should be deterministic for Unicode too
        vec2 = generate_mock_embedding(text, dimension=16)
        self.assertEqual(vec, vec2)

    def test_large_dimension(self):
        """Test generation of large dimension vectors."""
        vec = generate_mock_embedding("test", dimension=4096)
        self.assertEqual(len(vec), 4096)


class TestMockEmbeddingsBatch(unittest.TestCase):
    """Test batch mock embedding generation."""

    def test_batch_returns_correct_count(self):
        """Test that batch returns one vector per input text."""
        texts = ["class Foo {};", "class Bar {};", "void baz() {}"]
        vecs = generate_mock_embeddings(texts, dimension=64)

        self.assertEqual(len(vecs), 3)
        for v in vecs:
            self.assertEqual(len(v), 64)

    def test_batch_determinism(self):
        """Test that batch output matches sequential single calls."""
        texts = ["alpha", "beta", "gamma"]

        batch_vecs = generate_mock_embeddings(texts, dimension=32)
        single_vecs = [generate_mock_embedding(t, dimension=32) for t in texts]

        for bv, sv in zip(batch_vecs, single_vecs):
            self.assertEqual(bv, sv)

    def test_batch_empty_list(self):
        """Test batch with empty input list."""
        vecs = generate_mock_embeddings([], dimension=16)
        self.assertEqual(vecs, [])

    def test_batch_single_text(self):
        """Test batch with a single text."""
        vecs = generate_mock_embeddings(["hello"], dimension=8)
        self.assertEqual(len(vecs), 1)
        self.assertEqual(len(vecs[0]), 8)

    def test_batch_default_dimension(self):
        """Test batch with default dimension (1536)."""
        vecs = generate_mock_embeddings(["test"])
        self.assertEqual(len(vecs), 1)
        self.assertEqual(len(vecs[0]), 1536)


class TestTruncateText(unittest.TestCase):
    """Test text truncation helper."""

    def test_short_text_unchanged(self):
        """Test that short text passes through unchanged."""
        text = "void foo() {}"
        self.assertEqual(truncate_text(text, max_chars=100), text)

    def test_exact_limit_unchanged(self):
        """Test that text at exactly the limit is unchanged."""
        text = "a" * 100
        self.assertEqual(truncate_text(text, max_chars=100), text)

    def test_over_limit_truncated(self):
        """Test that text over the limit is truncated."""
        text = "a" * 200
        result = truncate_text(text, max_chars=100)
        self.assertEqual(len(result), 100)
        self.assertEqual(result, "a" * 100)

    def test_empty_string(self):
        """Test that empty string passes through."""
        self.assertEqual(truncate_text("", max_chars=100), "")

    def test_default_max_chars(self):
        """Test with default max_chars from config (32764)."""
        from ingestion.config import MAX_EMBED_CHARS

        short = "x" * 100
        self.assertEqual(truncate_text(short), short)

        long_text = "x" * (MAX_EMBED_CHARS + 500)
        result = truncate_text(long_text)
        self.assertEqual(len(result), MAX_EMBED_CHARS)


class TestGetEmbeddingsRouter(unittest.TestCase):
    """Test the get_embeddings router function."""

    @patch("ingestion.embedding.USE_MOCK_EMBEDDING", True)
    def test_routes_to_mock_when_flag_true(self):
        """Test that USE_MOCK_EMBEDDING=True routes to mock backend."""
        texts = ["class Foo {};", "void bar() {}"]
        vecs = get_embeddings(texts, dimension=32)

        self.assertEqual(len(vecs), 2)
        for v in vecs:
            self.assertEqual(len(v), 32)

        # Verify determinism (mock property)
        vecs2 = get_embeddings(texts, dimension=32)
        self.assertEqual(vecs, vecs2)

    @patch("ingestion.embedding.USE_MOCK_EMBEDDING", False)
    @patch("ingestion.embedding.generate_real_embeddings")
    def test_routes_to_real_when_flag_false(self, mock_real_fn):
        """Test that USE_MOCK_EMBEDDING=False routes to real backend."""
        mock_real_fn.return_value = [[0.1] * 32, [0.2] * 32]

        texts = ["class Foo {};", "void bar() {}"]
        vecs = get_embeddings(texts, dimension=32)

        mock_real_fn.assert_called_once_with(texts, 32)
        self.assertEqual(len(vecs), 2)


class TestGenerateRealEmbeddings(unittest.TestCase):
    """Test real embedding generation (mocked OpenAI client)."""

    @patch("ingestion.embedding._get_openai_client")
    def test_calls_openai_with_correct_args(self, mock_get_client):
        """Test that the OpenAI client is called with correct parameters."""
        from ingestion.embedding import generate_real_embeddings

        # Build mock response
        mock_data_0 = MagicMock()
        mock_data_0.embedding = [0.1] * 16
        mock_data_1 = MagicMock()
        mock_data_1.embedding = [0.2] * 16

        mock_response = MagicMock()
        mock_response.data = [mock_data_0, mock_data_1]

        mock_client = MagicMock()
        mock_client.embeddings.create.return_value = mock_response
        mock_get_client.return_value = mock_client

        texts = ["hello", "world"]
        vecs = generate_real_embeddings(texts, dimension=16)

        self.assertEqual(len(vecs), 2)
        self.assertEqual(vecs[0], [0.1] * 16)
        self.assertEqual(vecs[1], [0.2] * 16)

        # Verify create was called
        mock_client.embeddings.create.assert_called_once()

    @patch("ingestion.embedding.EMBEDDING_MODEL", "openai/text-embedding-3-small")
    @patch("ingestion.embedding._get_openai_client")
    def test_passes_dimensions_for_text_embedding_3(self, mock_get_client):
        """Test that dimensions kwarg is passed for text-embedding-3 models."""
        from ingestion.embedding import generate_real_embeddings

        mock_data = MagicMock()
        mock_data.embedding = [0.5] * 256

        mock_response = MagicMock()
        mock_response.data = [mock_data]

        mock_client = MagicMock()
        mock_client.embeddings.create.return_value = mock_response
        mock_get_client.return_value = mock_client

        generate_real_embeddings(["test"], dimension=256)

        call_kwargs = mock_client.embeddings.create.call_args
        self.assertIn("dimensions", call_kwargs.kwargs)
        self.assertEqual(call_kwargs.kwargs["dimensions"], 256)

    @patch("ingestion.embedding.EMBEDDING_MODEL", "some-other/model")
    @patch("ingestion.embedding._get_openai_client")
    def test_omits_dimensions_for_non_text_embedding_3(self, mock_get_client):
        """Test that dimensions kwarg is NOT passed for non text-embedding-3 models."""
        from ingestion.embedding import generate_real_embeddings

        mock_data = MagicMock()
        mock_data.embedding = [0.5] * 1536

        mock_response = MagicMock()
        mock_response.data = [mock_data]

        mock_client = MagicMock()
        mock_client.embeddings.create.return_value = mock_response
        mock_get_client.return_value = mock_client

        generate_real_embeddings(["test"], dimension=1536)

        call_kwargs = mock_client.embeddings.create.call_args
        self.assertNotIn("dimensions", call_kwargs.kwargs)

    @patch("ingestion.embedding._get_openai_client")
    def test_truncates_long_text(self, mock_get_client):
        """Test that very long texts are truncated before sending to API."""
        from ingestion.embedding import generate_real_embeddings
        from ingestion.config import MAX_EMBED_CHARS

        mock_data = MagicMock()
        mock_data.embedding = [0.1] * 1536

        mock_response = MagicMock()
        mock_response.data = [mock_data]

        mock_client = MagicMock()
        mock_client.embeddings.create.return_value = mock_response
        mock_get_client.return_value = mock_client

        # Create text longer than MAX_EMBED_CHARS
        long_text = "x" * (MAX_EMBED_CHARS + 5000)
        generate_real_embeddings([long_text], dimension=1536)

        # Verify the text sent to the API was truncated
        call_kwargs = mock_client.embeddings.create.call_args
        sent_texts = call_kwargs.kwargs["input"]
        self.assertEqual(len(sent_texts[0]), MAX_EMBED_CHARS)


class TestRetryBehavior(unittest.TestCase):
    """Test that only transient/network errors trigger retries."""

    @patch("ingestion.embedding._get_openai_client")
    def test_retries_on_rate_limit_error(self, mock_get_client):
        """Test that RateLimitError (429) triggers a retry."""
        import openai
        from ingestion.embedding import generate_real_embeddings

        mock_data = MagicMock()
        mock_data.embedding = [0.1] * 16

        mock_response = MagicMock()
        mock_response.data = [mock_data]

        mock_client = MagicMock()
        # Fail once with 429, then succeed
        mock_client.embeddings.create.side_effect = [
            openai.RateLimitError(
                message="Rate limit exceeded",
                response=MagicMock(status_code=429),
                body=None,
            ),
            mock_response,
        ]
        mock_get_client.return_value = mock_client

        vecs = generate_real_embeddings(["test"], dimension=16)

        self.assertEqual(len(vecs), 1)
        self.assertEqual(mock_client.embeddings.create.call_count, 2)

    @patch("ingestion.embedding._get_openai_client")
    def test_retries_on_internal_server_error(self, mock_get_client):
        """Test that InternalServerError (5xx) triggers a retry."""
        import openai
        from ingestion.embedding import generate_real_embeddings

        mock_data = MagicMock()
        mock_data.embedding = [0.2] * 16

        mock_response = MagicMock()
        mock_response.data = [mock_data]

        mock_client = MagicMock()
        mock_client.embeddings.create.side_effect = [
            openai.InternalServerError(
                message="Internal server error",
                response=MagicMock(status_code=500),
                body=None,
            ),
            mock_response,
        ]
        mock_get_client.return_value = mock_client

        vecs = generate_real_embeddings(["test"], dimension=16)

        self.assertEqual(len(vecs), 1)
        self.assertEqual(mock_client.embeddings.create.call_count, 2)

    @patch("ingestion.embedding._get_openai_client")
    def test_retries_on_api_connection_error(self, mock_get_client):
        """Test that APIConnectionError triggers a retry."""
        import openai
        from ingestion.embedding import generate_real_embeddings

        mock_data = MagicMock()
        mock_data.embedding = [0.3] * 16

        mock_response = MagicMock()
        mock_response.data = [mock_data]

        mock_client = MagicMock()
        mock_client.embeddings.create.side_effect = [
            openai.APIConnectionError(request=MagicMock()),
            mock_response,
        ]
        mock_get_client.return_value = mock_client

        vecs = generate_real_embeddings(["test"], dimension=16)

        self.assertEqual(len(vecs), 1)
        self.assertEqual(mock_client.embeddings.create.call_count, 2)

    @patch("ingestion.embedding._get_openai_client")
    def test_no_retry_on_authentication_error(self, mock_get_client):
        """Test that AuthenticationError (401) does NOT retry — fails immediately."""
        import openai
        from ingestion.embedding import generate_real_embeddings

        mock_client = MagicMock()
        mock_client.embeddings.create.side_effect = openai.AuthenticationError(
            message="Invalid API key",
            response=MagicMock(status_code=401),
            body=None,
        )
        mock_get_client.return_value = mock_client

        with self.assertRaises(openai.AuthenticationError):
            generate_real_embeddings(["test"], dimension=16)

        # Should have been called exactly once — no retries
        self.assertEqual(mock_client.embeddings.create.call_count, 1)

    @patch("ingestion.embedding._get_openai_client")
    def test_no_retry_on_bad_request_error(self, mock_get_client):
        """Test that BadRequestError (400) does NOT retry — fails immediately."""
        import openai
        from ingestion.embedding import generate_real_embeddings

        mock_client = MagicMock()
        mock_client.embeddings.create.side_effect = openai.BadRequestError(
            message="Invalid input",
            response=MagicMock(status_code=400),
            body=None,
        )
        mock_get_client.return_value = mock_client

        with self.assertRaises(openai.BadRequestError):
            generate_real_embeddings(["test"], dimension=16)

        # Should have been called exactly once — no retries
        self.assertEqual(mock_client.embeddings.create.call_count, 1)


class TestOpenAIClientSingleton(unittest.TestCase):
    """Test the module-level client initialization."""

    def test_missing_api_key_raises(self):
        """Test that missing API key raises ValueError."""
        from ingestion.embedding import _get_openai_client

        import ingestion.embedding as emb

        # Save and clear state
        original_client = emb._openai_client
        original_key = emb.OPENROUTER_API_KEY
        emb._openai_client = None
        emb.OPENROUTER_API_KEY = ""

        try:
            with self.assertRaises(ValueError) as ctx:
                _get_openai_client()
            self.assertIn("OPENROUTER_API_KEY", str(ctx.exception))
        finally:
            # Restore
            emb._openai_client = original_client
            emb.OPENROUTER_API_KEY = original_key


if __name__ == "__main__":
    unittest.main()
