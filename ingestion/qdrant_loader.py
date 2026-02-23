"""
Qdrant vector database ingestion pipeline.

Provides functions to connect to Qdrant, initialize collections,
and upsert code entity embeddings with deterministic IDs.

Embedding generation is performed in **batches** — one API call per
chunk of entities — by delegating to ``ingestion.embedding.get_embeddings``.
"""

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, asdict, field
from typing import Iterable, Iterator, List, Dict, Any, Optional, Callable

from qdrant_client import QdrantClient, models
from core.startup_config import (
    load_docker_compose_config,
    resolve_service_port,
    resolve_strict_config_validation,
)

from ingestion.config import (
    DOCKER_COMPOSE_PATH,
    QDRANT_HOST,
    QDRANT_DEFAULT_PORT,
    DEFAULT_COLLECTION_NAME,
    DEFAULT_VECTOR_DIMENSION,
    DEFAULT_DISTANCE_METRIC,
    DEFAULT_BATCH_SIZE,
    DEFAULT_MAX_EMBED_CHARS_PER_BATCH,
    UUID_NAMESPACE,
    CONNECTION_RETRIES,
    CONNECTION_RETRY_DELAY,
    UPSERT_RETRIES,
    UPSERT_RETRY_BASE_DELAY,
)
from ingestion.embedding import get_embeddings

logger = logging.getLogger(__name__)


@dataclass
class IngestionStats:
    """Statistics for an ingestion operation."""

    points_uploaded: int = 0
    points_attempted: int = 0
    batches_sent: int = 0
    batches_failed: int = 0
    errors: int = 0
    retry_attempts: int = 0
    dropped_by_reason: Dict[str, int] = field(default_factory=dict)

    def add_drop(self, reason: str, count: int = 1) -> None:
        """Increment dropped/skipped counters by reason."""
        self.dropped_by_reason[reason] = self.dropped_by_reason.get(reason, 0) + count

    def success_rate(self) -> float:
        """Return successful write rate over attempted points."""
        if self.points_attempted <= 0:
            return 1.0
        return self.points_uploaded / self.points_attempted

    def to_slo_report(self) -> Dict[str, Any]:
        """Return SLO-style ingestion report payload."""
        return {
            "points_attempted": self.points_attempted,
            "points_uploaded": self.points_uploaded,
            "points_failed": self.points_attempted - self.points_uploaded,
            "success_rate": round(self.success_rate(), 6),
            "batches_sent": self.batches_sent,
            "batches_failed": self.batches_failed,
            "retry_attempts": self.retry_attempts,
            "errors": self.errors,
            "dropped_by_reason": dict(sorted(self.dropped_by_reason.items())),
        }

    def __str__(self) -> str:
        """String representation of stats."""
        return (
            f"IngestionStats(points={self.points_uploaded}, "
            f"batches={self.batches_sent}, errors={self.errors}, "
            f"success_rate={self.success_rate():.2%})"
        )


def _extract_vector_size(collection_info: Any) -> Optional[int]:
    """Extract vector dimension from a Qdrant collection info payload."""
    vectors = getattr(getattr(collection_info, "config", None), "params", None)
    vectors = getattr(vectors, "vectors", None)
    if vectors is None:
        return None

    # Most common path: VectorParams object with `.size`.
    size = getattr(vectors, "size", None)
    if isinstance(size, int):
        return size

    # Named vectors may be represented as dict-like.
    if isinstance(vectors, dict):
        for value in vectors.values():
            candidate = getattr(value, "size", None)
            if isinstance(candidate, int):
                return candidate

    return None


def _upsert_with_retries(
    client: QdrantClient,
    collection_name: str,
    points: List[models.PointStruct],
    max_retries: int = UPSERT_RETRIES,
    retry_base_delay: float = UPSERT_RETRY_BASE_DELAY,
) -> tuple[bool, int]:
    """Upsert a batch with bounded exponential backoff retries."""
    for attempt in range(1, max_retries + 1):
        try:
            client.upsert(collection_name=collection_name, points=points)
            return True, attempt - 1
        except Exception as e:
            if attempt == max_retries:
                logger.error(
                    "Upsert failed after %d attempts for batch size %d: %s",
                    max_retries,
                    len(points),
                    e,
                )
                return False, attempt - 1

            sleep_seconds = retry_base_delay * (2 ** (attempt - 1))
            logger.warning(
                "Upsert attempt %d/%d failed for batch size %d: %s. Retrying in %.2fs.",
                attempt,
                max_retries,
                len(points),
                e,
                sleep_seconds,
            )
            time.sleep(sleep_seconds)

    return False, max_retries - 1


def get_qdrant_client(strict_config: Optional[bool] = None) -> QdrantClient:
    """Connect to Qdrant instance using configuration from docker-compose.yml.

    Parses the docker-compose configuration to extract the Qdrant port,
    then establishes a connection with retry logic.

    Returns:
        Connected QdrantClient instance.

    Raises:
        ConnectionError: If unable to connect after retries.
        FileNotFoundError: If docker-compose.yml not found.

    Example:
        >>> client = get_qdrant_client()
        >>> collections = client.get_collections()
    """
    if strict_config is None:
        strict_config = resolve_strict_config_validation(default=False)

    compose = load_docker_compose_config(
        DOCKER_COMPOSE_PATH,
        strict=strict_config,
    )
    port = resolve_service_port(
        compose_data=compose,
        service_name="qdrant",
        container_port=6333,
        default_port=QDRANT_DEFAULT_PORT,
        strict=strict_config,
    )
    logger.debug("Extracted Qdrant port from docker-compose: %d", port)

    # Attempt connection with retries
    for attempt in range(CONNECTION_RETRIES):
        try:
            client = QdrantClient(host=QDRANT_HOST, port=port)

            # Verify connectivity with a lightweight operation
            client.get_collections()

            logger.info(f"Connected to Qdrant at {QDRANT_HOST}:{port}")
            return client

        except Exception as e:
            logger.warning(
                f"Connection attempt {attempt + 1}/{CONNECTION_RETRIES} failed: {e}"
            )
            if attempt < CONNECTION_RETRIES - 1:
                time.sleep(CONNECTION_RETRY_DELAY)
            else:
                raise ConnectionError(
                    f"Failed to connect to Qdrant at {QDRANT_HOST}:{port} "
                    f"after {CONNECTION_RETRIES} attempts"
                ) from e

    # Should never reach here, but satisfy type checker
    raise ConnectionError("Unexpected error in connection logic")


def init_collection(
    client: QdrantClient,
    collection_name: str = DEFAULT_COLLECTION_NAME,
    vector_dimension: int = DEFAULT_VECTOR_DIMENSION,
    recreate: bool = False,
) -> None:
    """Initialize or recreate a Qdrant collection for code embeddings.

    Args:
        client: Connected QdrantClient instance.
        collection_name: Name of the collection to create/verify.
        vector_dimension: Dimension of embedding vectors.
        recreate: If True, delete existing collection and recreate.
                 If False, create only if it doesn't exist (idempotent).

    Raises:
        Exception: If collection creation fails.

    Example:
        >>> client = get_qdrant_client()
        >>> init_collection(client, "my_code", 1536, recreate=True)
    """
    try:
        # Check if collection exists
        exists = client.collection_exists(collection_name)

        if exists and recreate:
            logger.info(f"Deleting existing collection '{collection_name}'")
            client.delete_collection(collection_name)
            exists = False

        if not exists:
            logger.info(
                f"Creating collection '{collection_name}' "
                f"(dimension={vector_dimension}, metric={DEFAULT_DISTANCE_METRIC})"
            )

            client.create_collection(
                collection_name=collection_name,
                vectors_config=models.VectorParams(
                    size=vector_dimension,
                    distance=DEFAULT_DISTANCE_METRIC,
                ),
            )

            logger.info(
                f"Collection '{collection_name}' created, now creating payload indices..."
            )

            # CRITICAL: Create payload indices for hybrid search performance.
            # Without these, Qdrant performs full-scan on filtered queries.
            indexed_fields = [
                "global_uri",
                "repo_name",
                "file_path",
                "entity_type",
                "entity_name",
            ]

            for field_name in indexed_fields:
                client.create_payload_index(
                    collection_name=collection_name,
                    field_name=field_name,
                    field_schema=models.PayloadSchemaType.KEYWORD,
                )
                logger.debug(f"Created KEYWORD index on field '{field_name}'")

            logger.info(
                f"Collection '{collection_name}' created with "
                f"{len(indexed_fields)} payload indices"
            )
        else:
            collection_info = client.get_collection(collection_name)
            existing_dimension = _extract_vector_size(collection_info)
            if existing_dimension is not None and existing_dimension != vector_dimension:
                raise ValueError(
                    f"Collection '{collection_name}' dimension mismatch: "
                    f"existing={existing_dimension}, requested={vector_dimension}. "
                    "Use recreate=True or provide matching dimension."
                )
            logger.info(
                f"Collection '{collection_name}' already exists, skipping creation"
            )

    except Exception as e:
        logger.error(f"Failed to initialize collection '{collection_name}': {e}")
        raise


def generate_point_id(global_uri: str) -> str:
    """Generate a deterministic UUID for a code entity.

    Uses UUIDv5 with a fixed namespace to ensure the same global_uri
    always produces the same UUID. This enables idempotent upserts —
    re-ingesting the same entity will overwrite the existing point,
    not create duplicates.

    Args:
        global_uri: The unique identifier for the code entity.

    Returns:
        String representation of UUIDv5.

    Example:
        >>> id1 = generate_point_id("repo::file.cpp::Class::Foo")
        >>> id2 = generate_point_id("repo::file.cpp::Class::Foo")
        >>> id1 == id2  # Same URI always produces same UUID
        True
    """
    return str(uuid.uuid5(UUID_NAMESPACE, global_uri))


def _build_embed_text(entity_dict: Dict[str, Any]) -> str:
    """Build the text string that will be sent to the embedding model.

    Concatenates ``docstring`` (if present) and ``code_text`` so the
    embedding captures both natural-language intent and syntactic
    structure.

    Args:
        entity_dict: Dictionary representation of an ExtractedEntity.

    Returns:
        Concatenated text for embedding.
    """
    docstring = entity_dict.get("docstring")
    code_text = entity_dict["code_text"]

    if docstring:
        return f"{docstring}\n{code_text}"
    return code_text


def build_point(
    entity_dict: Dict[str, Any],
    vector: List[float],
) -> models.PointStruct:
    """Build a Qdrant point from an entity dictionary and a pre-computed vector.

    This function is intentionally **embedding-agnostic** — it receives the
    vector that was already generated externally (by the batch embedding
    call in ``ingest_entities``).

    Args:
        entity_dict: Dictionary representation of ExtractedEntity.
        vector: Pre-computed embedding vector for this entity.

    Returns:
        PointStruct ready for upsert to Qdrant.

    Raises:
        KeyError: If required fields missing from entity_dict.

    Example:
        >>> entity = {"global_uri": "...", "code_text": "...", ...}
        >>> vec = [0.1, 0.2, ...]
        >>> point = build_point(entity, vec)
    """
    global_uri = entity_dict["global_uri"]

    # Generate deterministic point ID
    point_id = generate_point_id(global_uri)

    # Build payload — store ALL entity metadata
    payload = {
        "global_uri": entity_dict["global_uri"],
        "repo_name": entity_dict["repo_name"],
        "file_path": entity_dict["file_path"],
        "entity_type": entity_dict["entity_type"],
        "entity_name": entity_dict["entity_name"],
        "docstring": entity_dict.get("docstring"),  # Can be None
        "code_text": entity_dict["code_text"],
        "start_line": entity_dict["start_line"],
        "end_line": entity_dict["end_line"],
        "is_templated": entity_dict["is_templated"],
    }

    return models.PointStruct(
        id=point_id,
        vector=vector,
        payload=payload,
    )


def iter_jsonl_entity_batches(
    file_path: str,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> Iterator[List[Dict[str, Any]]]:
    """Stream JSONL entities from disk in bounded batches.

    Args:
        file_path: Path to JSONL file.
        batch_size: Number of entities per yielded batch.

    Yields:
        Batches of parsed entity dictionaries.

    Raises:
        FileNotFoundError: If file does not exist.
        json.JSONDecodeError: If any JSONL row is malformed.
    """
    current: List[Dict[str, Any]] = []

    with open(file_path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue

            try:
                entity = json.loads(line)
            except json.JSONDecodeError as e:
                logger.error("Invalid JSON on line %d: %s", line_num, e)
                raise

            current.append(entity)
            if len(current) >= batch_size:
                yield current
                current = []

    if current:
        yield current


def _iter_embedding_batches(
    entities: Iterable[Dict[str, Any]],
    stats: IngestionStats,
    max_entities_per_batch: int,
    max_chars_per_batch: int,
) -> Iterator[tuple[List[Dict[str, Any]], List[str]]]:
    """Yield embedding-ready batches bounded by entity count and char budget."""
    batch_entities: List[Dict[str, Any]] = []
    batch_texts: List[str] = []
    batch_chars = 0

    for entity in entities:
        try:
            text = _build_embed_text(entity)
        except KeyError as e:
            logger.error(
                "Missing required field %s in entity %s — skipping",
                e,
                entity.get("global_uri", "unknown"),
            )
            stats.errors += 1
            stats.add_drop("missing_required_field", 1)
            continue

        text_len = len(text)
        should_flush = (
            len(batch_entities) >= max_entities_per_batch
            or (batch_entities and (batch_chars + text_len > max_chars_per_batch))
        )
        if should_flush:
            yield batch_entities, batch_texts
            batch_entities = []
            batch_texts = []
            batch_chars = 0

        batch_entities.append(entity)
        batch_texts.append(text)
        batch_chars += text_len

    if batch_entities:
        yield batch_entities, batch_texts


def ingest_entities(
    entities: Iterable[Dict[str, Any]],
    client: QdrantClient,
    collection_name: str = DEFAULT_COLLECTION_NAME,
    embed_fn: Optional[Callable[[List[str], int], List[List[float]]]] = None,
    dimension: int = DEFAULT_VECTOR_DIMENSION,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_embed_chars_per_batch: int = DEFAULT_MAX_EMBED_CHARS_PER_BATCH,
    upsert_retries: int = UPSERT_RETRIES,
    upsert_retry_base_delay: float = UPSERT_RETRY_BASE_DELAY,
) -> IngestionStats:
    """Ingest code entities into Qdrant with batched embedding and upload.

    For each batch of entities:

    1. Extract the embedding text (``docstring + code_text``) for every
       entity in the batch.
    2. Call ``embed_fn(texts, dimension)`` **exactly once** per batch to
       get all vectors in a single API round-trip.
    3. Pair each entity with its vector via ``zip()`` and build
       ``PointStruct`` objects.
    4. Upsert the batch to Qdrant.

    Args:
        entities: Iterable of entity dictionaries (from ``ExtractedEntity.to_dict()``).
        client: Connected QdrantClient instance.
        collection_name: Target collection name.
        embed_fn: Batch embedding function ``(List[str], int) -> List[List[float]]``.
                 If None, uses ``get_embeddings`` (the config-aware router).
        dimension: Vector dimension for embeddings.
        batch_size: Max number of points per embedding/upsert batch.
        max_embed_chars_per_batch: Soft cap for total characters per embedding
            request batch.
        upsert_retries: Number of retry attempts for Qdrant upsert.
        upsert_retry_base_delay: Base delay for exponential backoff between retries.

    Returns:
        IngestionStats with upload metrics.

    Example:
        >>> entities = [entity.to_dict() for entity in extracted_entities]
        >>> client = get_qdrant_client()
        >>> stats = ingest_entities(entities, client)
        >>> print(f"Uploaded {stats.points_uploaded} points")
    """
    if embed_fn is None:
        embed_fn = get_embeddings

    stats = IngestionStats()
    for valid_entities, embed_texts in _iter_embedding_batches(
        entities=entities,
        stats=stats,
        max_entities_per_batch=batch_size,
        max_chars_per_batch=max_embed_chars_per_batch,
    ):

        # ------------------------------------------------------------------
        # Step 2: Generate embeddings — ONE call per batch
        # ------------------------------------------------------------------
        try:
            vectors = embed_fn(embed_texts, dimension)
        except Exception as e:
            logger.error(
                "Batch embedding failed for %d texts: %s", len(embed_texts), e
            )
            stats.errors += len(valid_entities)
            stats.add_drop("embedding_failure", len(valid_entities))
            continue

        # Sanity check: API must return exactly one vector per text
        if len(vectors) != len(valid_entities):
            logger.error(
                "Embedding count mismatch: expected %d, got %d — skipping batch",
                len(valid_entities),
                len(vectors),
            )
            stats.errors += len(valid_entities)
            stats.add_drop("embedding_count_mismatch", len(valid_entities))
            continue

        # ------------------------------------------------------------------
        # Step 3: Build PointStruct objects from (entity, vector) pairs
        # ------------------------------------------------------------------
        batch_points: List[models.PointStruct] = []

        for entity, vector in zip(valid_entities, vectors):
            # Verify vector dimension
            if len(vector) != dimension:
                logger.error(
                    "Dimension mismatch for %s: expected %d, got %d — skipping",
                    entity.get("global_uri", "unknown"),
                    dimension,
                    len(vector),
                )
                stats.errors += 1
                stats.add_drop("vector_dimension_mismatch", 1)
                continue

            try:
                point = build_point(entity, vector)
                batch_points.append(point)
            except Exception as e:
                logger.error(
                    "Failed to build point for %s: %s",
                    entity.get("global_uri", "unknown"),
                    e,
                )
                stats.errors += 1
                stats.add_drop("point_build_failure", 1)

        # ------------------------------------------------------------------
        # Step 4: Upsert batch to Qdrant
        # ------------------------------------------------------------------
        if batch_points:
            stats.points_attempted += len(batch_points)
            upsert_ok, retries_used = _upsert_with_retries(
                client=client,
                collection_name=collection_name,
                points=batch_points,
                max_retries=upsert_retries,
                retry_base_delay=upsert_retry_base_delay,
            )
            stats.retry_attempts += retries_used
            if upsert_ok:
                stats.points_uploaded += len(batch_points)
                stats.batches_sent += 1

                logger.info(
                    "Uploaded batch %d: %d points (total: %d)",
                    stats.batches_sent,
                    len(batch_points),
                    stats.points_uploaded,
                )

            else:
                uri_preview = [
                    point.payload.get("global_uri", "unknown")
                    for point in batch_points[:3]
                    if point.payload is not None
                ]
                logger.error(
                    "Dropping failed upsert batch of %d points. URI preview: %s",
                    len(batch_points),
                    uri_preview,
                )
                stats.errors += len(batch_points)
                stats.batches_failed += 1
                stats.add_drop("upsert_failed", len(batch_points))

    logger.info("Ingestion complete: %s", stats)
    logger.info("Ingestion SLO report: %s", json.dumps(stats.to_slo_report(), sort_keys=True))
    return stats


def ingest_from_jsonl(
    file_path: str,
    client: QdrantClient,
    collection_name: str = DEFAULT_COLLECTION_NAME,
    embed_fn: Optional[Callable[[List[str], int], List[List[float]]]] = None,
    dimension: int = DEFAULT_VECTOR_DIMENSION,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_embed_chars_per_batch: int = DEFAULT_MAX_EMBED_CHARS_PER_BATCH,
    upsert_retries: int = UPSERT_RETRIES,
    upsert_retry_base_delay: float = UPSERT_RETRY_BASE_DELAY,
) -> IngestionStats:
    """Ingest code entities from a JSONL file into Qdrant.

    Streams a JSONL file (one JSON object per line) in bounded chunks and
    delegates chunk ingestion to ``ingest_entities()``.

    Args:
        file_path: Path to JSONL file.
        client: Connected QdrantClient instance.
        collection_name: Target collection name.
        embed_fn: Batch embedding function. If None, uses ``get_embeddings``.
        dimension: Vector dimension for embeddings.
        batch_size: Number of points to upload per batch.
        max_embed_chars_per_batch: Soft cap for total characters per embedding
            request batch.
        upsert_retries: Number of retry attempts for Qdrant upsert.
        upsert_retry_base_delay: Base delay for exponential backoff between retries.

    Returns:
        IngestionStats with upload metrics.

    Raises:
        FileNotFoundError: If JSONL file not found.
        json.JSONDecodeError: If JSONL is malformed.

    Example:
        >>> client = get_qdrant_client()
        >>> stats = ingest_from_jsonl("entities.jsonl", client)
    """
    logger.info(f"Reading entities from {file_path}")

    if not os.path.isfile(file_path):
        logger.error(f"JSONL file not found: {file_path}")
        raise FileNotFoundError(file_path)

    aggregate = IngestionStats()

    for entity_batch in iter_jsonl_entity_batches(file_path=file_path, batch_size=batch_size):
        batch_stats = ingest_entities(
            entities=entity_batch,
            client=client,
            collection_name=collection_name,
            embed_fn=embed_fn,
            dimension=dimension,
            batch_size=batch_size,
            max_embed_chars_per_batch=max_embed_chars_per_batch,
            upsert_retries=upsert_retries,
            upsert_retry_base_delay=upsert_retry_base_delay,
        )
        aggregate.points_uploaded += batch_stats.points_uploaded
        aggregate.batches_sent += batch_stats.batches_sent
        aggregate.errors += batch_stats.errors

    logger.info("JSONL ingestion complete: %s", aggregate)
    logger.info(
        "JSONL ingestion SLO report: %s",
        json.dumps(aggregate.to_slo_report(), sort_keys=True),
    )
    return aggregate


def main() -> None:
    """Manual end-to-end verification of the ingestion pipeline."""

    # Configure logging for manual run
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    logger.info("=" * 80)
    logger.info(" Qdrant Ingestion Pipeline - Manual Verification")
    logger.info("=" * 80)

    try:
        # Import extraction module
        from extraction.extractor import extract_file

        # Parse test_torture.h
        logger.info("\n1. Extracting entities from test_torture.h...")
        entities_obj = extract_file(
            "extraction/tests/fixtures/test_torture.h", "rtc_engine"
        )

        # Convert to dict list
        entities = [asdict(e) for e in entities_obj]
        logger.info(f"   Extracted {len(entities)} entities")

        # Connect to Qdrant
        logger.info("\n2. Connecting to Qdrant...")
        client = get_qdrant_client()

        # Initialize collection (recreate to start fresh)
        logger.info("\n3. Initializing collection...")
        init_collection(client, collection_name="test_code", recreate=True)

        # Ingest entities
        logger.info("\n4. Ingesting entities...")
        stats = ingest_entities(entities, client, collection_name="test_code")

        # Verify by scrolling collection
        logger.info("\n5. Verifying upload...")
        scroll_result = client.scroll(collection_name="test_code", limit=10)

        points = scroll_result[0]
        logger.info(f"   Retrieved {len(points)} points from Qdrant:")
        for point in points:
            payload = point.payload
            if payload is not None:
                uri = payload.get("global_uri", "unknown")
                entity_type = payload.get("entity_type", "unknown")
            else:
                uri = "unknown"
                entity_type = "unknown"
            logger.info(f"     - {entity_type}: {uri}")

        logger.info("\n" + "=" * 80)
        logger.info(" Manual verification complete!")
        logger.info("=" * 80)

    except Exception as e:
        logger.error(f"\nManual verification failed: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    main()
