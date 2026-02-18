"""
Mock embedding generator for code entities.

Provides deterministic pseudo-embeddings for testing and development.
Will be replaced with real LLM API calls in production.
"""

import hashlib
import random
from typing import List


def generate_mock_embedding(text: str, dimension: int = 1536) -> List[float]:
    """Generate a deterministic mock embedding vector from text.
    
    Uses MD5 hash of the input text as a seed for the random number generator,
    ensuring that the same text always produces the exact same vector across
    different Python interpreter sessions.
    
    CRITICAL: This uses cryptographic hashing (MD5) instead of Python's built-in
    hash() function, which uses hash randomization and changes every interpreter
    restart. This is essential for idempotent re-runs of the ingestion pipeline.
    
    Args:
        text: Input text to generate embedding for.
        dimension: Vector dimension (default 1536 for OpenAI text-embedding-3-small).
        
    Returns:
        List of floats in range [-1.0, 1.0] with length = dimension.
        
    Example:
        >>> vec1 = generate_mock_embedding("class Foo {};", 8)
        >>> vec2 = generate_mock_embedding("class Foo {};", 8)
        >>> vec1 == vec2  # Same text always produces same vector
        True
        >>> len(vec1)
        8
    """
    # Use MD5 hash for stable, deterministic seeding
    # MD5 is sufficient here since we only need determinism, not cryptographic security
    hash_digest = hashlib.md5(text.encode('utf-8')).hexdigest()
    
    # Convert hex digest to integer seed
    seed = int(hash_digest, 16)
    
    # Create seeded random generator
    rng = random.Random(seed)
    
    # Generate vector with values in [-1.0, 1.0]
    vector = [rng.uniform(-1.0, 1.0) for _ in range(dimension)]
    
    return vector
