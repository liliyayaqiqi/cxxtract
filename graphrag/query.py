"""
Graph query functions for dependency analysis and blast radius calculation.

Provides high-level query APIs that will be exposed via MCP in Layer 3.
Uses Neo4j's index-free adjacency for O(1) traversal performance.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Literal, Optional, Sequence

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

    status: Literal["ok", "missing_root", "empty_result", "ambiguous_root"] = "ok"
    reason: str = ""
    ambiguous_candidates: list[str] = field(default_factory=list)
    next_cursor: Optional[str] = None
    query_timeout_s: float = DEFAULT_QUERY_TIMEOUT_S


@dataclass
class AffectedEntity:
    """An entity affected by a change (upstream) or depended upon (downstream)."""
    
    identity_key: str
    global_uri: str
    scip_symbol: str
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
    """Decode pagination cursor format '<depth>|<identity_key>'."""
    parts = cursor.split("|", 1)
    if len(parts) != 2:
        raise ValueError("Invalid cursor format. Expected '<depth>|<identity_key>'")
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


def _resolve_entity_selector(
    *,
    identity_key: Optional[str],
    scip_symbol: Optional[str],
    owner_repo: Optional[str],
    global_uri: Optional[str],
) -> tuple[str, dict[str, object], str, str]:
    """Build Cypher predicate/params for selecting root entities.

    Selection precedence:
    1. ``identity_key`` (unique entity)
    2. ``scip_symbol`` (+ optional ``owner_repo``)
    3. ``global_uri`` (symbol family; may map to multiple overload entities)
    """
    selectors = [
        identity_key is not None,
        scip_symbol is not None,
        global_uri is not None,
    ]
    if sum(selectors) > 1:
        raise ValueError(
            "Provide only one of identity_key, scip_symbol, or global_uri"
        )

    if identity_key is not None:
        return (
            "start.identity_key = $entity_id",
            {"entity_id": identity_key},
            identity_key,
            "identity_key",
        )

    if scip_symbol is not None:
        predicate = "start.scip_symbol = $scip_symbol"
        params: dict[str, object] = {"scip_symbol": scip_symbol}
        if owner_repo is not None:
            predicate += " AND start.owner_repo = $owner_repo"
            params["owner_repo"] = owner_repo
            origin = f"{owner_repo}:{scip_symbol}"
        else:
            origin = scip_symbol
        return predicate, params, origin, "scip_symbol"

    if global_uri is not None:
        # global_uri is a symbol-family identifier and may correspond to
        # multiple entities (e.g., overloaded functions).
        return (
            "start.global_uri = $global_uri",
            {"global_uri": global_uri},
            global_uri,
            "global_uri",
        )

    raise ValueError("One of identity_key, scip_symbol, or global_uri is required")


def _resolve_root_node(
    *,
    driver: Driver,
    selector_predicate: str,
    selector_params: dict[str, object],
    selector_type: str,
    repo_name: Optional[str],
    metadata: QueryMetadata,
    query_timeout_s: float,
) -> Optional[dict[str, str]]:
    """Resolve a unique root node or annotate metadata with an error state."""
    with driver.session() as session:
        records = list(
            session.run(
                Query(
                    f"""
                    MATCH (start:Entity)
                    WHERE {selector_predicate}
                      AND ($repo_name IS NULL OR start.repo_name = $repo_name)
                    RETURN
                        elementId(start) AS node_id,
                        start.identity_key AS identity_key,
                        start.global_uri AS global_uri
                    ORDER BY identity_key ASC, global_uri ASC
                    """,
                    timeout=query_timeout_s,
                ),
                **selector_params,
                repo_name=repo_name,
            )
        )

    if not records:
        metadata.status = "missing_root"
        metadata.reason = "origin_not_found"
        return None

    if len(records) > 1:
        candidates: list[str] = []
        seen: set[str] = set()
        for rec in records:
            candidate = rec.get("identity_key") or rec.get("global_uri")
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            candidates.append(candidate)
        candidates.sort()
        metadata.status = "ambiguous_root"
        metadata.reason = f"ambiguous_{selector_type}"
        metadata.ambiguous_candidates = candidates
        return None

    return {
        "node_id": records[0]["node_id"],
        "identity_key": records[0].get("identity_key") or records[0]["global_uri"],
        "global_uri": records[0]["global_uri"],
    }


def _build_shortest_path_pattern(
    direction: Literal["upstream", "downstream"],
    rel_types: Sequence[str],
    max_depth: int,
) -> str:
    rel_expr = "|".join(rel_types)
    if direction == "upstream":
        return f"(start)<-[:{rel_expr}*1..{max_depth}]-(target)"
    return f"(start)-[:{rel_expr}*1..{max_depth}]->(target)"


def calculate_blast_radius(
    global_uri: Optional[str] = None,
    driver: Optional[Driver] = None,
    max_depth: int = 5,
    direction: Literal["upstream", "downstream"] = "upstream",
    repo_name: Optional[str] = None,
    relationship_types: Optional[Sequence[str]] = None,
    max_results: Optional[int] = None,
    cursor: Optional[str] = None,
    query_timeout_s: float = DEFAULT_QUERY_TIMEOUT_S,
    *,
    identity_key: Optional[str] = None,
    scip_symbol: Optional[str] = None,
    owner_repo: Optional[str] = None,
) -> BlastRadiusResult:
    """Calculate the blast radius for a given code entity.
    
    **Upstream** (direction="upstream"): What breaks if I change this entity?
    Returns all entities that depend on the given entity (callers, subclasses, etc.).
    
    **Downstream** (direction="downstream"): What does this entity depend on?
    Returns all entities that the given entity depends on (callees, base classes, etc.).
    
    Uses a two-phase traversal strategy:
    1) bounded BFS expansion to find distinct affected nodes + min depth
    2) shortestPath reconstruction only for the returned page
    
    Args:
        global_uri: Legacy selector. Represents a symbol family, not always a
            unique entity (overloads may share the same value).
        driver: Connected Neo4j driver.
        max_depth: Maximum number of hops to traverse (default 5).
        direction: "upstream" or "downstream".
        repo_name: Optional repository filter for start + affected entities.
        relationship_types: Optional allowed edge types to traverse.
        max_results: Optional page size limit.
        cursor: Optional pagination cursor from previous call.
        query_timeout_s: Per-query timeout in seconds.
        identity_key: Preferred unique selector for one entity.
        scip_symbol: Alternative selector (optionally disambiguated by owner_repo).
        owner_repo: Owner repo filter when selecting by scip_symbol.
        
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
    if driver is None:
        raise ValueError("driver is required")

    selector_predicate, selector_params, origin_id, selector_type = _resolve_entity_selector(
        identity_key=identity_key,
        scip_symbol=scip_symbol,
        owner_repo=owner_repo,
        global_uri=global_uri,
    )
    logger.info(
        "Calculating %s blast radius for %s (max_depth=%d)",
        direction,
        origin_id,
        max_depth,
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
        origin_uri=origin_id,
        direction=direction,
        metadata=QueryMetadata(query_timeout_s=query_timeout_s),
    )

    root_node = _resolve_root_node(
        driver=driver,
        selector_predicate=selector_predicate,
        selector_params=selector_params,
        selector_type=selector_type,
        repo_name=repo_name,
        metadata=result.metadata,
        query_timeout_s=query_timeout_s,
    )
    if root_node is None:
        return result

    if not rel_types:
        result.metadata.status = "empty_result"
        result.metadata.reason = "no_relationship_types_requested"
        return result

    if direction == "upstream":
        expand_query = Query(
            """
            UNWIND $frontier AS frontier_node_id
            MATCH (frontier:Entity)
            WHERE elementId(frontier) = frontier_node_id
            MATCH (affected:Entity)-[r]->(frontier)
            WHERE type(r) IN $rel_types
            RETURN DISTINCT
                elementId(affected) AS node_id,
                affected.identity_key AS identity_key,
                affected.global_uri AS uri,
                affected.scip_symbol AS scip_symbol,
                affected.entity_type AS type,
                affected.entity_name AS name,
                affected.file_path AS file,
                affected.repo_name AS repo_name
            """,
            timeout=query_timeout_s,
        )
    else:
        expand_query = Query(
            """
            UNWIND $frontier AS frontier_node_id
            MATCH (frontier:Entity)
            WHERE elementId(frontier) = frontier_node_id
            MATCH (frontier)-[r]->(affected:Entity)
            WHERE type(r) IN $rel_types
            RETURN DISTINCT
                elementId(affected) AS node_id,
                affected.identity_key AS identity_key,
                affected.global_uri AS uri,
                affected.scip_symbol AS scip_symbol,
                affected.entity_type AS type,
                affected.entity_name AS name,
                affected.file_path AS file,
                affected.repo_name AS repo_name
            """,
            timeout=query_timeout_s,
        )

    frontier_node_ids = [root_node["node_id"]]
    visited_node_ids = {root_node["node_id"]}
    discovered: dict[str, dict[str, object]] = {}

    with driver.session() as session:
        for depth in range(1, max_depth + 1):
            if not frontier_node_ids:
                break
            records = list(
                session.run(
                    expand_query,
                    frontier=frontier_node_ids,
                    rel_types=rel_types,
                )
            )
            next_frontier: list[str] = []
            for record in records:
                node_id = record["node_id"]
                if node_id in visited_node_ids:
                    continue
                visited_node_ids.add(node_id)
                next_frontier.append(node_id)
                identity = record.get("identity_key") or record["uri"]
                discovered[identity] = {
                    "identity_key": identity,
                    "uri": record["uri"],
                    "scip_symbol": record.get("scip_symbol") or "",
                    "type": record["type"],
                    "name": record["name"],
                    "file": record["file"],
                    "repo_name": record.get("repo_name"),
                    "depth": depth,
                    "node_id": node_id,
                }
            frontier_node_ids = next_frontier

        filtered_records = [
            rec
            for rec in discovered.values()
            if repo_name is None or rec.get("repo_name") == repo_name
        ]
        filtered_records.sort(
            key=lambda rec: (int(rec["depth"]), str(rec["identity_key"]))
        )
        if cursor_depth is not None and cursor_uri is not None:
            filtered_records = [
                rec
                for rec in filtered_records
                if int(rec["depth"]) > cursor_depth
                or (
                    int(rec["depth"]) == cursor_depth
                    and str(rec["identity_key"]) > cursor_uri
                )
            ]

        next_cursor = None
        visible_records = filtered_records
        if max_results is not None and len(filtered_records) > max_results:
            visible_records = filtered_records[:max_results]
            last_visible = visible_records[-1]
            next_cursor = _encode_cursor(
                int(last_visible["depth"]),
                str(last_visible["identity_key"]),
            )

        chain_by_node_id: dict[str, list[str]] = {}
        if visible_records:
            shortest_path_pattern = _build_shortest_path_pattern(
                direction=direction,
                rel_types=rel_types,
                max_depth=max_depth,
            )
            chain_query = Query(
                f"""
                MATCH (start:Entity)
                WHERE elementId(start) = $start_node_id
                UNWIND $target_node_ids AS target_node_id
                MATCH (target:Entity)
                WHERE elementId(target) = target_node_id
                MATCH path = shortestPath({shortest_path_pattern})
                RETURN elementId(target) AS node_id, [rel IN relationships(path) | type(rel)] AS chain
                """,
                timeout=query_timeout_s,
            )
            chain_records = session.run(
                chain_query,
                start_node_id=root_node["node_id"],
                target_node_ids=[str(rec["node_id"]) for rec in visible_records],
            )
            for rec in chain_records:
                chain_by_node_id[rec["node_id"]] = rec.get("chain") or []

        for record in visible_records:
            result.affected_entities.append(
                AffectedEntity(
                    identity_key=str(record["identity_key"]),
                    global_uri=str(record["uri"]),
                    scip_symbol=str(record.get("scip_symbol") or ""),
                    entity_type=str(record["type"]),
                    entity_name=str(record["name"]),
                    file_path=str(record["file"]),
                    depth=int(record["depth"]),
                    relationship_chain=chain_by_node_id.get(str(record["node_id"]), []),
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
    global_uri: Optional[str] = None,
    driver: Optional[Driver] = None,
    include_non_entity: bool = False,
    relationship_types: Optional[Sequence[str]] = None,
    repo_name: Optional[str] = None,
    max_results: int = DEFAULT_NEIGHBOR_LIMIT,
    query_timeout_s: float = DEFAULT_QUERY_TIMEOUT_S,
    *,
    identity_key: Optional[str] = None,
    scip_symbol: Optional[str] = None,
    owner_repo: Optional[str] = None,
) -> dict[str, object]:
    """Get immediate neighbors (1-hop) of an entity.
    
    Args:
        global_uri: Legacy selector for symbol family.
        driver: Connected Neo4j driver.
        include_non_entity: Whether to include non-Entity neighbor nodes.
        relationship_types: Optional relationship types to include.
        repo_name: Optional repository filter.
        max_results: Max inbound/outbound records to return each.
        query_timeout_s: Per-query timeout in seconds.
        identity_key: Preferred unique selector.
        scip_symbol: Alternative selector.
        owner_repo: Optional owner-repo filter with scip_symbol.
        
    Returns:
        Dict with keys "inbound" and "outbound", each containing a list of
        neighbor dicts with "uri", "type", "relationship" fields.
    """
    if max_results < 1:
        raise ValueError("max_results must be >= 1")
    if driver is None:
        raise ValueError("driver is required")
    rel_types = _sanitize_relationship_types(relationship_types)
    selector_predicate, selector_params, _, selector_type = _resolve_entity_selector(
        identity_key=identity_key,
        scip_symbol=scip_symbol,
        owner_repo=owner_repo,
        global_uri=global_uri,
    )

    metadata = QueryMetadata(query_timeout_s=query_timeout_s)
    root_node = _resolve_root_node(
        driver=driver,
        selector_predicate=selector_predicate,
        selector_params=selector_params,
        selector_type=selector_type,
        repo_name=repo_name,
        metadata=metadata,
        query_timeout_s=query_timeout_s,
    )
    if root_node is None:
        return {"inbound": [], "outbound": [], "metadata": metadata}

    with driver.session() as session:
        src_label = "" if include_non_entity else ":Entity"
        tgt_label = "" if include_non_entity else ":Entity"

        inbound_query = """
        MATCH (start:Entity)
        WHERE elementId(start) = $start_node_id
        MATCH (src{src_label})-[r]->(start)
        WHERE type(r) IN $rel_types
          AND ($repo_name IS NULL OR src.repo_name = $repo_name)
        RETURN DISTINCT src.identity_key AS identity_key, src.global_uri AS uri, src.entity_type AS type, type(r) AS relationship
        ORDER BY relationship ASC, identity_key ASC
        LIMIT $limit
        """.format(src_label=src_label)
        inbound = [
            {
                "identity_key": rec.get("identity_key") or rec["uri"],
                "uri": rec["uri"],
                "type": rec["type"],
                "relationship": rec["relationship"],
            }
            for rec in session.run(
                Query(inbound_query, timeout=query_timeout_s),
                start_node_id=root_node["node_id"],
                rel_types=rel_types,
                repo_name=repo_name,
                limit=max_results,
            )
        ]

        outbound_query = """
        MATCH (start:Entity)
        WHERE elementId(start) = $start_node_id
        MATCH (start)-[r]->(tgt{tgt_label})
        WHERE type(r) IN $rel_types
          AND ($repo_name IS NULL OR tgt.repo_name = $repo_name)
        RETURN DISTINCT tgt.identity_key AS identity_key, tgt.global_uri AS uri, tgt.entity_type AS type, type(r) AS relationship
        ORDER BY relationship ASC, identity_key ASC
        LIMIT $limit
        """.format(tgt_label=tgt_label)
        outbound = [
            {
                "identity_key": rec.get("identity_key") or rec["uri"],
                "uri": rec["uri"],
                "type": rec["type"],
                "relationship": rec["relationship"],
            }
            for rec in session.run(
                Query(outbound_query, timeout=query_timeout_s),
                start_node_id=root_node["node_id"],
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
    global_uri: Optional[str] = None,
    driver: Optional[Driver] = None,
    repo_name: Optional[str] = None,
    max_results: int = DEFAULT_NEIGHBOR_LIMIT,
    query_timeout_s: float = DEFAULT_QUERY_TIMEOUT_S,
    *,
    identity_key: Optional[str] = None,
    scip_symbol: Optional[str] = None,
    owner_repo: Optional[str] = None,
) -> dict[str, object]:
    """Get the full inheritance hierarchy for a class.
    
    Args:
        global_uri: Legacy selector for symbol family.
        driver: Connected Neo4j driver.
        repo_name: Optional repository filter.
        max_results: Max ancestors/descendants to return each.
        query_timeout_s: Per-query timeout in seconds.
        identity_key: Preferred unique selector.
        scip_symbol: Alternative selector.
        owner_repo: Optional owner-repo filter with scip_symbol.
        
    Returns:
        Dict with keys "ancestors" (base classes) and "descendants" (derived classes).
    """
    if max_results < 1:
        raise ValueError("max_results must be >= 1")
    if driver is None:
        raise ValueError("driver is required")
    selector_predicate, selector_params, _, selector_type = _resolve_entity_selector(
        identity_key=identity_key,
        scip_symbol=scip_symbol,
        owner_repo=owner_repo,
        global_uri=global_uri,
    )

    metadata = QueryMetadata(query_timeout_s=query_timeout_s)
    root_node = _resolve_root_node(
        driver=driver,
        selector_predicate=selector_predicate,
        selector_params=selector_params,
        selector_type=selector_type,
        repo_name=repo_name,
        metadata=metadata,
        query_timeout_s=query_timeout_s,
    )
    if root_node is None:
        return {"ancestors": [], "descendants": [], "metadata": metadata}

    with driver.session() as session:
        ancestors_query = f"""
        MATCH (root:Entity)
        WHERE elementId(root) = $root_node_id
        MATCH path = (root)-[:INHERITS*1..]->(ancestor:Entity)
        WHERE $repo_name IS NULL OR ancestor.repo_name = $repo_name
        WITH ancestor.identity_key AS identity_key, ancestor.global_uri AS uri, min(length(path)) AS depth
        RETURN uri, identity_key
        ORDER BY depth ASC, identity_key ASC
        LIMIT $limit
        """
        ancestors = [
            rec["uri"]
            for rec in session.run(
                Query(ancestors_query, timeout=query_timeout_s),
                root_node_id=root_node["node_id"],
                repo_name=repo_name,
                limit=max_results,
            )
        ]

        descendants_query = f"""
        MATCH (root:Entity)
        WHERE elementId(root) = $root_node_id
        MATCH path = (descendant:Entity)-[:INHERITS*1..]->(root)
        WHERE $repo_name IS NULL OR descendant.repo_name = $repo_name
        WITH descendant.identity_key AS identity_key, descendant.global_uri AS uri, min(length(path)) AS depth
        RETURN uri, identity_key
        ORDER BY depth ASC, identity_key ASC
        LIMIT $limit
        """
        descendants = [
            rec["uri"]
            for rec in session.run(
                Query(descendants_query, timeout=query_timeout_s),
                root_node_id=root_node["node_id"],
                repo_name=repo_name,
                limit=max_results,
            )
        ]

    if not ancestors and not descendants:
        metadata.status = "empty_result"
        metadata.reason = "no_inheritance_relationships"

    return {"ancestors": ancestors, "descendants": descendants, "metadata": metadata}


def fetch_qdrant_documents_for_identity_keys(
    identity_keys: Sequence[str],
    qdrant_client: Any,
    collection_name: Optional[str] = None,
    with_vectors: bool = False,
) -> dict[str, dict[str, Any]]:
    """Fetch Qdrant payloads by identity_key for Neo4j join results."""
    if collection_name is None:
        from ingestion.config import DEFAULT_COLLECTION_NAME

        collection_name = DEFAULT_COLLECTION_NAME
    from ingestion.qdrant_loader import fetch_documents_by_identity_keys

    return fetch_documents_by_identity_keys(
        client=qdrant_client,
        identity_keys=identity_keys,
        collection_name=collection_name,
        with_vectors=with_vectors,
    )


def fetch_qdrant_documents_for_affected_entities(
    affected_entities: Sequence[AffectedEntity],
    qdrant_client: Any,
    collection_name: Optional[str] = None,
    with_vectors: bool = False,
) -> dict[str, dict[str, Any]]:
    """Fetch Qdrant payloads for blast-radius entities using identity_key."""
    identity_keys = [entity.identity_key for entity in affected_entities if entity.identity_key]
    return fetch_qdrant_documents_for_identity_keys(
        identity_keys=identity_keys,
        qdrant_client=qdrant_client,
        collection_name=collection_name,
        with_vectors=with_vectors,
    )
