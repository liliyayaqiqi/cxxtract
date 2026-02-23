"""
Graph query functions for dependency analysis and blast radius calculation.

Provides high-level query APIs that will be exposed via MCP in Layer 3.
Uses Neo4j's index-free adjacency for O(1) traversal performance.
"""

import logging
from dataclasses import dataclass, field
from typing import Literal, Optional, Sequence

from neo4j import Driver, Query

logger = logging.getLogger(__name__)

DEFAULT_QUERY_TIMEOUT_S = 10.0
DEFAULT_NEIGHBOR_LIMIT = 200
DEFAULT_BLAST_RELATIONSHIP_TYPES = (
    "CALLS",
    "USES_TYPE",
    "INHERITS",
    "OVERRIDES",
)


@dataclass
class QueryMetadata:
    """Typed metadata describing query outcome and pagination."""

    status: Literal["ok", "missing_root", "empty_result"] = "ok"
    reason: str = ""
    next_cursor: Optional[str] = None
    query_timeout_s: float = DEFAULT_QUERY_TIMEOUT_S


@dataclass
class AffectedEntity:
    """An entity affected by a change (upstream) or depended upon (downstream)."""
    
    global_uri: str
    entity_type: str
    entity_name: str
    file_path: str
    depth: int                      # Number of hops from origin
    relationship_chain: list[str]   # e.g., ["CALLS", "INHERITS"]


@dataclass
class BlastRadiusResult:
    """Result of a blast radius calculation."""
    
    origin_uri: str
    direction: Literal["upstream", "downstream"]
    affected_entities: list[AffectedEntity] = field(default_factory=list)
    total_count: int = 0
    max_depth_reached: int = 0
    metadata: QueryMetadata = field(default_factory=QueryMetadata)


def _sanitize_relationship_types(
    relationship_types: Optional[Sequence[str]],
) -> list[str]:
    """Validate and normalize relationship type filters."""
    if relationship_types is None:
        return list(DEFAULT_BLAST_RELATIONSHIP_TYPES)

    normalized = []
    for rel in relationship_types:
        rel_upper = rel.upper()
        if rel_upper not in DEFAULT_BLAST_RELATIONSHIP_TYPES:
            raise ValueError(f"Unsupported relationship type filter: {rel}")
        if rel_upper not in normalized:
            normalized.append(rel_upper)
    return normalized


def _decode_cursor(cursor: str) -> tuple[int, str]:
    """Decode pagination cursor format '<depth>|<global_uri>'."""
    parts = cursor.split("|", 1)
    if len(parts) != 2:
        raise ValueError("Invalid cursor format. Expected '<depth>|<global_uri>'")
    try:
        depth = int(parts[0])
    except ValueError as exc:
        raise ValueError("Invalid cursor depth component") from exc
    if depth < 0 or not parts[1]:
        raise ValueError("Invalid cursor content")
    return depth, parts[1]


def _encode_cursor(depth: int, uri: str) -> str:
    """Encode pagination cursor."""
    return f"{depth}|{uri}"


def calculate_blast_radius(
    global_uri: str,
    driver: Driver,
    max_depth: int = 5,
    direction: Literal["upstream", "downstream"] = "upstream",
    repo_name: Optional[str] = None,
    relationship_types: Optional[Sequence[str]] = None,
    max_results: Optional[int] = None,
    cursor: Optional[str] = None,
    query_timeout_s: float = DEFAULT_QUERY_TIMEOUT_S,
) -> BlastRadiusResult:
    """Calculate the blast radius for a given code entity.
    
    **Upstream** (direction="upstream"): What breaks if I change this entity?
    Returns all entities that depend on the given entity (callers, subclasses, etc.).
    
    **Downstream** (direction="downstream"): What does this entity depend on?
    Returns all entities that the given entity depends on (callees, base classes, etc.).
    
    Uses variable-length path traversal with configurable depth limit.
    Leverages Neo4j's index-free adjacency for O(1) edge traversal.
    
    Args:
        global_uri: The Global URI of the entity to analyze.
        driver: Connected Neo4j driver.
        max_depth: Maximum number of hops to traverse (default 5).
        direction: "upstream" or "downstream".
        repo_name: Optional repository filter for start + affected entities.
        relationship_types: Optional allowed edge types to traverse.
        max_results: Optional page size limit.
        cursor: Optional pagination cursor from previous call.
        query_timeout_s: Per-query timeout in seconds.
        
    Returns:
        BlastRadiusResult with all affected entities.
        
    Example:
        >>> driver = get_neo4j_driver()
        >>> result = calculate_blast_radius(
        ...     "yaml-cpp::src/binary.cpp::Function::YAML::EncodeBase64",
        ...     driver,
        ...     max_depth=3,
        ...     direction="upstream"
        ... )
        >>> for entity in result.affected_entities:
        ...     print(f"{entity.depth} hops: {entity.entity_name}")
    """
    logger.info(
        f"Calculating {direction} blast radius for {global_uri} "
        f"(max_depth={max_depth})"
    )
    if max_depth < 1:
        raise ValueError("max_depth must be >= 1")
    if max_results is not None and max_results < 1:
        raise ValueError("max_results must be >= 1")

    rel_types = _sanitize_relationship_types(relationship_types)
    cursor_depth: Optional[int] = None
    cursor_uri: Optional[str] = None
    if cursor is not None:
        cursor_depth, cursor_uri = _decode_cursor(cursor)
    
    result = BlastRadiusResult(
        origin_uri=global_uri,
        direction=direction,
        metadata=QueryMetadata(query_timeout_s=query_timeout_s),
    )

    with driver.session() as session:
        root_query = Query(
            """
            MATCH (start:Entity {global_uri: $uri})
            WHERE $repo_name IS NULL OR start.repo_name = $repo_name
            RETURN start.global_uri AS uri
            LIMIT 1
            """,
            timeout=query_timeout_s,
        )
        root_record = session.run(
            root_query, uri=global_uri, repo_name=repo_name
        ).single()
        if root_record is None:
            result.metadata.status = "missing_root"
            result.metadata.reason = "origin_not_found"
            return result

        if not rel_types:
            result.metadata.status = "empty_result"
            result.metadata.reason = "no_relationship_types_requested"
            return result
    
    if direction == "upstream":
        pattern = "(start)<-[:%s*1..%d]-(affected:Entity)"
    else:
        pattern = "(start)-[:%s*1..%d]->(affected:Entity)"
    pattern = pattern % ("|".join(rel_types), max_depth)

    query = f"""
    MATCH (start:Entity {{global_uri: $uri}})
    WHERE $repo_name IS NULL OR start.repo_name = $repo_name
    MATCH path = {pattern}
    WHERE $repo_name IS NULL OR affected.repo_name = $repo_name
    WITH affected, length(path) AS depth, [rel IN relationships(path) | type(rel)] AS chain
    WITH affected, depth, chain, reduce(acc = '', rel IN chain | acc + '|' + rel) AS chain_key
    ORDER BY affected.global_uri ASC, depth ASC, chain_key ASC
    WITH affected, collect({{depth: depth, chain: chain}})[0] AS best
    WITH affected, best.depth AS depth, best.chain AS chain
    WHERE $cursor_depth IS NULL
      OR depth > $cursor_depth
      OR (depth = $cursor_depth AND affected.global_uri > $cursor_uri)
    RETURN
        affected.global_uri AS uri,
        affected.entity_type AS type,
        affected.entity_name AS name,
        affected.file_path AS file,
        depth,
        chain
    ORDER BY depth ASC, uri ASC
    """
    if max_results is not None:
        query += "\nLIMIT $limit"

    run_params = {
        "uri": global_uri,
        "repo_name": repo_name,
        "cursor_depth": cursor_depth,
        "cursor_uri": cursor_uri,
    }
    if max_results is not None:
        run_params["limit"] = max_results + 1

    with driver.session() as session:
        records = list(
            session.run(
                Query(query, timeout=query_timeout_s),
                **run_params,
            )
        )

        next_cursor = None
        visible_records = records
        if max_results is not None and len(records) > max_results:
            visible_records = records[:max_results]
            last_visible = visible_records[-1]
            next_cursor = _encode_cursor(last_visible["depth"], last_visible["uri"])

        for record in visible_records:
            result.affected_entities.append(
                AffectedEntity(
                    global_uri=record["uri"],
                    entity_type=record["type"],
                    entity_name=record["name"],
                    file_path=record["file"],
                    depth=record["depth"],
                    relationship_chain=record["chain"],
                )
            )
        
        result.total_count = len(result.affected_entities)
        result.max_depth_reached = (
            max(e.depth for e in result.affected_entities)
            if result.affected_entities
            else 0
        )
        result.metadata.next_cursor = next_cursor
        if result.total_count == 0:
            result.metadata.status = "empty_result"
            result.metadata.reason = "no_matching_paths"
    
    logger.info(
        f"Blast radius: {result.total_count} affected entities, "
        f"max depth {result.max_depth_reached}"
    )
    
    return result


def get_entity_neighbors(
    global_uri: str,
    driver: Driver,
    include_non_entity: bool = False,
    relationship_types: Optional[Sequence[str]] = None,
    repo_name: Optional[str] = None,
    max_results: int = DEFAULT_NEIGHBOR_LIMIT,
    query_timeout_s: float = DEFAULT_QUERY_TIMEOUT_S,
) -> dict[str, object]:
    """Get immediate neighbors (1-hop) of an entity.
    
    Args:
        global_uri: The Global URI to query.
        driver: Connected Neo4j driver.
        include_non_entity: Whether to include non-Entity neighbor nodes.
        relationship_types: Optional relationship types to include.
        repo_name: Optional repository filter.
        max_results: Max inbound/outbound records to return each.
        query_timeout_s: Per-query timeout in seconds.
        
    Returns:
        Dict with keys "inbound" and "outbound", each containing a list of
        neighbor dicts with "uri", "type", "relationship" fields.
    """
    if max_results < 1:
        raise ValueError("max_results must be >= 1")
    rel_types = _sanitize_relationship_types(relationship_types)

    metadata = QueryMetadata(query_timeout_s=query_timeout_s)
    with driver.session() as session:
        root_record = session.run(
            Query(
                """
                MATCH (start:Entity {global_uri: $uri})
                WHERE $repo_name IS NULL OR start.repo_name = $repo_name
                RETURN start.global_uri AS uri
                LIMIT 1
                """,
                timeout=query_timeout_s,
            ),
            uri=global_uri,
            repo_name=repo_name,
        ).single()
        if root_record is None:
            metadata.status = "missing_root"
            metadata.reason = "origin_not_found"
            return {"inbound": [], "outbound": [], "metadata": metadata}

        src_label = "" if include_non_entity else ":Entity"
        tgt_label = "" if include_non_entity else ":Entity"

        inbound_query = f"""
        MATCH (src{src_label})-[r]->(tgt:Entity {{global_uri: $uri}})
        WHERE type(r) IN $rel_types
          AND ($repo_name IS NULL OR src.repo_name = $repo_name)
        RETURN src.global_uri AS uri, src.entity_type AS type, type(r) AS relationship
        ORDER BY relationship ASC, uri ASC
        LIMIT $limit
        """
        inbound = [
            {"uri": rec["uri"], "type": rec["type"], "relationship": rec["relationship"]}
            for rec in session.run(
                Query(inbound_query, timeout=query_timeout_s),
                uri=global_uri,
                rel_types=rel_types,
                repo_name=repo_name,
                limit=max_results,
            )
        ]

        outbound_query = f"""
        MATCH (src:Entity {{global_uri: $uri}})-[r]->(tgt{tgt_label})
        WHERE type(r) IN $rel_types
          AND ($repo_name IS NULL OR tgt.repo_name = $repo_name)
        RETURN tgt.global_uri AS uri, tgt.entity_type AS type, type(r) AS relationship
        ORDER BY relationship ASC, uri ASC
        LIMIT $limit
        """
        outbound = [
            {"uri": rec["uri"], "type": rec["type"], "relationship": rec["relationship"]}
            for rec in session.run(
                Query(outbound_query, timeout=query_timeout_s),
                uri=global_uri,
                rel_types=rel_types,
                repo_name=repo_name,
                limit=max_results,
            )
        ]

    if not inbound and not outbound:
        metadata.status = "empty_result"
        metadata.reason = "no_neighbors"

    return {"inbound": inbound, "outbound": outbound, "metadata": metadata}


def get_inheritance_tree(
    global_uri: str,
    driver: Driver,
    repo_name: Optional[str] = None,
    max_results: int = DEFAULT_NEIGHBOR_LIMIT,
    query_timeout_s: float = DEFAULT_QUERY_TIMEOUT_S,
) -> dict[str, object]:
    """Get the full inheritance hierarchy for a class.
    
    Args:
        global_uri: The Global URI of a Class/Struct entity.
        driver: Connected Neo4j driver.
        repo_name: Optional repository filter.
        max_results: Max ancestors/descendants to return each.
        query_timeout_s: Per-query timeout in seconds.
        
    Returns:
        Dict with keys "ancestors" (base classes) and "descendants" (derived classes).
    """
    if max_results < 1:
        raise ValueError("max_results must be >= 1")

    metadata = QueryMetadata(query_timeout_s=query_timeout_s)
    with driver.session() as session:
        root_record = session.run(
            Query(
                """
                MATCH (root:Entity {global_uri: $uri})
                WHERE $repo_name IS NULL OR root.repo_name = $repo_name
                RETURN root.global_uri AS uri
                LIMIT 1
                """,
                timeout=query_timeout_s,
            ),
            uri=global_uri,
            repo_name=repo_name,
        ).single()
        if root_record is None:
            metadata.status = "missing_root"
            metadata.reason = "origin_not_found"
            return {"ancestors": [], "descendants": [], "metadata": metadata}

        ancestors_query = """
        MATCH path = (child:Entity {global_uri: $uri})-[:INHERITS*1..]->(ancestor:Entity)
        WHERE $repo_name IS NULL OR ancestor.repo_name = $repo_name
        WITH ancestor.global_uri AS uri, min(length(path)) AS depth
        RETURN uri
        ORDER BY depth ASC, uri ASC
        LIMIT $limit
        """
        ancestors = [
            rec["uri"]
            for rec in session.run(
                Query(ancestors_query, timeout=query_timeout_s),
                uri=global_uri,
                repo_name=repo_name,
                limit=max_results,
            )
        ]

        descendants_query = """
        MATCH path = (descendant:Entity)-[:INHERITS*1..]->(base:Entity {global_uri: $uri})
        WHERE $repo_name IS NULL OR descendant.repo_name = $repo_name
        WITH descendant.global_uri AS uri, min(length(path)) AS depth
        RETURN uri
        ORDER BY depth ASC, uri ASC
        LIMIT $limit
        """
        descendants = [
            rec["uri"]
            for rec in session.run(
                Query(descendants_query, timeout=query_timeout_s),
                uri=global_uri,
                repo_name=repo_name,
                limit=max_results,
            )
        ]

    if not ancestors and not descendants:
        metadata.status = "empty_result"
        metadata.reason = "no_inheritance_relationships"

    return {"ancestors": ancestors, "descendants": descendants, "metadata": metadata}
