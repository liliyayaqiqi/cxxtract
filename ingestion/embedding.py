"""
Embedding generator for code entities — mock and real (OpenRouter) backends.

All public functions operate on **batches** (List[str] -> List[List[float]])
to align with the OpenAI API's native batch input support and minimize
round-trips during ingestion.

The module-level router ``get_embeddings()`` inspects the
``USE_MOCK_EMBEDDING`` config flag and dispatches accordingly.
"""

import hashlib
import logging
import random
from typing import List

import openai
from openai import OpenAI
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ingestion.config import (
    OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
    EMBEDDING_MODEL,
    USE_MOCK_EMBEDDING,
    MAX_EMBED_CHARS,
    EMBEDDING_MAX_RETRIES,
    EMBEDDING_RETRY_MIN_WAIT,
    EMBEDDING_RETRY_MAX_WAIT,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level OpenAI client singleton (lazy — created on first real call)
# ---------------------------------------------------------------------------
_openai_client: OpenAI | None = None


def _get_openai_client() -> OpenAI:
    """Return the module-level OpenAI client, creating it on first use.

    The client is configured to point at the OpenRouter base URL with the
    required attribution headers.

    Returns:
        Configured OpenAI client instance.

    Raises:
        ValueError: If ``OPENROUTER_API_KEY`` is empty.
    """
    global _openai_client

    if _openai_client is not None:
        return _openai_client

    if not OPENROUTER_API_KEY:
        raise ValueError(
            "OPENROUTER_API_KEY is not set. "
            "Add it to your .env file or export it as an environment variable."
        )

    _openai_client = OpenAI(
        api_key=OPENROUTER_API_KEY,
        base_url=OPENROUTER_BASE_URL,
        default_headers={
            "HTTP-Referer": "https://github.com/cxxtract",
            "X-Title": "CXXtract RAG",
        },
    )

    logger.info(
        "OpenAI client initialized (base_url=%s, model=%s)",
        OPENROUTER_BASE_URL,
        EMBEDDING_MODEL,
    )

    return _openai_client


# ---------------------------------------------------------------------------
# Text truncation helper
# ---------------------------------------------------------------------------

def truncate_text(text: str, max_chars: int = MAX_EMBED_CHARS) -> str:
    """Truncate text to fit within the model's token limit.

    Uses the conservative rule **1 token ~= 4 characters** so that
    ``max_chars = MAX_EMBED_TOKENS * 4``.  Truncation is performed by
    simple character slicing which is safe for UTF-8 strings in Python.

    Args:
        text: Raw input text.
        max_chars: Maximum character count (default from config).

    Returns:
        Truncated text, unchanged if already within limit.
    """
    if len(text) <= max_chars:
        return text

    logger.debug(
        "Truncating text from %d to %d chars (approx %d tokens)",
        len(text),
        max_chars,
        max_chars // 4,
    )
    return text[:max_chars]


# ---------------------------------------------------------------------------
# Mock embedding backend (deterministic, no network)
# ---------------------------------------------------------------------------

def generate_mock_embedding(text: str, dimension: int = 1536) -> List[float]:
    """Generate a single deterministic mock embedding vector from text.

    Provided for backward-compatibility with existing call-sites and tests
    that pass a single string.  Internally delegates to the batch variant.

    Args:
        text: Input text.
        dimension: Vector dimension (default 1536).

    Returns:
        List of floats in ``[-1.0, 1.0]`` with length ``dimension``.
    """
    return generate_mock_embeddings([text], dimension)[0]


def generate_mock_embeddings(
    texts: List[str],
    dimension: int = 1536,
) -> List[List[float]]:
    """Generate deterministic mock embedding vectors for a batch of texts.

    Uses MD5 hash of each input text as a seed for the random number
    generator, ensuring that the same text always produces the exact same
    vector across different Python interpreter sessions.

    Args:
        texts: List of input texts.
        dimension: Vector dimension (default 1536).

    Returns:
        List of embedding vectors, one per input text. Each vector has
        values in ``[-1.0, 1.0]`` with length ``dimension``.

    Example:
        >>> vecs = generate_mock_embeddings(["class Foo {};", "class Bar {};"], 8)
        >>> len(vecs)
        2
        >>> len(vecs[0])
        8
        >>> vecs[0] == generate_mock_embeddings(["class Foo {};"], 8)[0]
        True
    """
    vectors: List[List[float]] = []

    for text in texts:
        # MD5 for stable, deterministic seeding (not for security)
        hash_digest = hashlib.md5(text.encode("utf-8")).hexdigest()
        seed = int(hash_digest, 16)
        rng = random.Random(seed)
        vector = [rng.uniform(-1.0, 1.0) for _ in range(dimension)]
        vectors.append(vector)

    return vectors


# ---------------------------------------------------------------------------
# Real embedding backend (OpenRouter API)
# ---------------------------------------------------------------------------

# Only retry on transient / network-related errors.
# Non-retryable errors (AuthenticationError, BadRequestError, etc.)
# propagate immediately so callers get fast, actionable feedback.
_RETRYABLE_EXCEPTIONS = (
    openai.RateLimitError,        # 429 — too many requests
    openai.APIConnectionError,    # Network unreachable / DNS failure
    openai.APITimeoutError,       # Request timed out
    openai.InternalServerError,   # 5xx — transient server-side error
)


@retry(
    retry=retry_if_exception_type(_RETRYABLE_EXCEPTIONS),
    wait=wait_exponential(
        multiplier=1,
        min=EMBEDDING_RETRY_MIN_WAIT,
        max=EMBEDDING_RETRY_MAX_WAIT,
    ),
    stop=stop_after_attempt(EMBEDDING_MAX_RETRIES),
    reraise=True,
)
def generate_real_embeddings(
    texts: List[str],
    dimension: int = 1536,
) -> List[List[float]]:
    """Generate embedding vectors via the OpenRouter API.

    Sends the full ``texts`` list in a single API call (the OpenAI
    embeddings endpoint natively supports ``input: List[str]``).

    Every text is truncated to ``MAX_EMBED_CHARS`` **before** sending to
    guard against token-limit errors.

    The ``dimensions`` kwarg is ONLY forwarded when ``EMBEDDING_MODEL``
    contains ``"text-embedding-3"`` because non-OpenAI models hosted on
    OpenRouter do not accept it and will return a 400 error.

    Retries up to ``EMBEDDING_MAX_RETRIES`` times with exponential
    back-off, but **only** for transient / network-related errors:

    * ``RateLimitError``      (429)
    * ``APIConnectionError``  (network unreachable / DNS)
    * ``APITimeoutError``     (request timed out)
    * ``InternalServerError`` (5xx)

    Non-retryable errors (``AuthenticationError``, ``BadRequestError``,
    ``PermissionDeniedError``, ``NotFoundError``) propagate immediately.

    Args:
        texts: List of input texts.
        dimension: Desired vector dimension (only honoured for
                   text-embedding-3-* models).

    Returns:
        List of embedding vectors, one per input text.

    Raises:
        ValueError: If ``OPENROUTER_API_KEY`` is not configured.
        openai.RateLimitError: After exhausting retries on 429.
        openai.APIConnectionError: After exhausting retries on network errors.
        openai.APITimeoutError: After exhausting retries on timeouts.
        openai.InternalServerError: After exhausting retries on 5xx.
        openai.AuthenticationError: Immediately on invalid API key (no retry).
        openai.BadRequestError: Immediately on malformed request (no retry).
    """
    client = _get_openai_client()

    # Truncate every text to stay within token budget
    truncated_texts: List[str] = [truncate_text(t) for t in texts]

    # Build kwargs — conditionally include `dimensions`
    create_kwargs = {
        "input": truncated_texts,
        "model": EMBEDDING_MODEL,
    }

    if "text-embedding-3" in EMBEDDING_MODEL:
        create_kwargs["dimensions"] = dimension

    logger.debug(
        "Requesting embeddings for %d texts (model=%s, dimension=%s)",
        len(truncated_texts),
        EMBEDDING_MODEL,
        dimension if "text-embedding-3" in EMBEDDING_MODEL else "model-default",
    )

    response = client.embeddings.create(**create_kwargs)

    vectors: List[List[float]] = [data.embedding for data in response.data]

    logger.debug(
        "Received %d vectors (dimension=%d each)",
        len(vectors),
        len(vectors[0]) if vectors else 0,
    )

    return vectors


# ---------------------------------------------------------------------------
# Router: dispatches to mock or real based on config flag
# ---------------------------------------------------------------------------

def get_embeddings(
    texts: List[str],
    dimension: int = 1536,
) -> List[List[float]]:
    """Generate embeddings for a batch of texts using the configured backend.

    Inspects ``USE_MOCK_EMBEDDING`` from ``ingestion.config``:

    * **True**  -> ``generate_mock_embeddings()`` (deterministic, offline)
    * **False** -> ``generate_real_embeddings()``  (OpenRouter API call)

    This is the **only** function that ``qdrant_loader.py`` should call.

    Args:
        texts: List of input texts.
        dimension: Vector dimension (default 1536).

    Returns:
        List of embedding vectors, one per input text.
    """
    if USE_MOCK_EMBEDDING:
        logger.debug("Using MOCK embeddings for %d texts", len(texts))
        return generate_mock_embeddings(texts, dimension)

    logger.debug("Using REAL embeddings for %d texts", len(texts))
    return generate_real_embeddings(texts, dimension)
