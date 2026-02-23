"""
Neo4j graph database loader for C++ dependency graph.

Handles connection, schema initialization, and batch ingestion of
nodes and edges derived from SCIP index data.
"""

import logging
import time
from dataclasses import dataclass
from typing import Optional

from core.uri_contract import parse_global_uri
from neo4j import GraphDatabase, Driver, Session

from graphrag.config import (
    NEO4J_URI,
    NEO4J_USERNAME,
    NEO4J_PASSWORD,
    NEO4J_BATCH_SIZE,
    NEO4J_CONNECTION_RETRIES,
    NEO4J_CONNECTION_RETRY_DELAY,
)
from graphrag.scip_parser import ScipParseResult, ScipSymbolDef, ScipReference
from graphrag.symbol_mapper import (
    scip_symbol_to_global_uri,
    classify_symbol,
    parse_scip_symbol,
)

logger = logging.getLogger(__name__)


@dataclass
class GraphNode:
    """A node to be inserted into Neo4j."""
    
    global_uri: str
    repo_name: str
    file_path: str
    entity_type: str
    entity_name: str
    scip_symbol: str
    is_external: bool


@dataclass
class GraphEdge:
    """An edge to be inserted into Neo4j."""
    
    src_uri: str
    tgt_uri: str
    relationship_type: str  # INHERITS, CALLS, etc.


@dataclass
class IngestionStats:
    """Statistics for graph ingestion."""
    
    nodes_created: int = 0
    edges_created: int = 0
    batches_sent: int = 0
    errors: int = 0
    
    def __str__(self) -> str:
        return (
            f"GraphIngestionStats(nodes={self.nodes_created}, "
            f"edges={self.edges_created}, batches={self.batches_sent}, "
            f"errors={self.errors})"
        )


_TYPE_SYMBOLS = {"Class", "Struct"}
_IMPLEMENTATION_SRC_TYPES = {"Class", "Struct", "Function"}
_IMPLEMENTATION_TGT_TYPES = {"Class", "Struct", "Function"}
_ALLOWED_EDGE_TYPE_PAIRS: dict[str, set[tuple[str, str]]] = {
    "INHERITS": {
        ("Class", "Class"),
        ("Class", "Struct"),
        ("Struct", "Class"),
        ("Struct", "Struct"),
    },
    "OVERRIDES": {("Function", "Function")},
    "CALLS": {("Function", "Function")},
    "USES_TYPE": {
        ("Function", "Class"),
        ("Function", "Struct"),
        ("Class", "Class"),
        ("Class", "Struct"),
        ("Struct", "Class"),
        ("Struct", "Struct"),
    },
}


def _infer_implementation_edge_type(
    src_symbol: str,
    src_kind: int,
    target_symbol: str,
    target_kind: int,
) -> Optional[str]:
    """Map SCIP implementation relationships to graph edge types."""
    parsed_src = parse_scip_symbol(src_symbol, src_kind)
    parsed_tgt = parse_scip_symbol(target_symbol, target_kind)
    if parsed_src is None or parsed_tgt is None:
        return None
    if parsed_src.entity_type not in _IMPLEMENTATION_SRC_TYPES:
        return None
    if parsed_tgt.entity_type not in _IMPLEMENTATION_TGT_TYPES:
        return None

    if parsed_src.entity_type == "Function" and parsed_tgt.entity_type == "Function":
        return "OVERRIDES"
    if parsed_src.entity_type in _TYPE_SYMBOLS and parsed_tgt.entity_type in _TYPE_SYMBOLS:
        return "INHERITS"
    return None


def _infer_reference_edge_type(
    src_entity_type: str,
    tgt_entity_type: str,
    role: str,
) -> Optional[str]:
    """Map reference occurrences to graph edge types."""
    if role not in {"CALL", "READ", "WRITE"}:
        return None
    if tgt_entity_type in _TYPE_SYMBOLS:
        return "USES_TYPE"
    if src_entity_type == "Function" and tgt_entity_type == "Function":
        return "CALLS"
    return None


def _dedupe_nodes(nodes: list[GraphNode]) -> list[GraphNode]:
    """Deduplicate nodes by URI, preferring local definition over stub."""
    by_uri: dict[str, GraphNode] = {}
    for node in nodes:
        existing = by_uri.get(node.global_uri)
        if existing is None:
            by_uri[node.global_uri] = node
            continue
        if existing.is_external and not node.is_external:
            by_uri[node.global_uri] = node
    return list(by_uri.values())


def _dedupe_edges(edges: list[GraphEdge]) -> list[GraphEdge]:
    """Deduplicate edges by (src, target, relationship type)."""
    by_key: dict[tuple[str, str, str], GraphEdge] = {}
    for edge in edges:
        key = (edge.src_uri, edge.tgt_uri, edge.relationship_type)
        if key not in by_key:
            by_key[key] = edge
    return list(by_key.values())


def _validate_edges(edges: list[GraphEdge]) -> list[GraphEdge]:
    """Drop edges that violate ingestion invariants."""
    valid: list[GraphEdge] = []
    for edge in edges:
        try:
            src = parse_global_uri(edge.src_uri)
            tgt = parse_global_uri(edge.tgt_uri)
        except ValueError:
            logger.debug(f"Dropping edge with malformed URI: {edge}")
            continue

        src_type = src["entity_type"]
        tgt_type = tgt["entity_type"]

        if edge.relationship_type == "CALLS" and src_type == "File":
            logger.debug(f"Dropping invalid CALLS edge from File node: {edge}")
            continue

        allowed_pairs = _ALLOWED_EDGE_TYPE_PAIRS.get(edge.relationship_type)
        if allowed_pairs is not None and (src_type, tgt_type) not in allowed_pairs:
            logger.debug(
                "Dropping impossible edge type pair %s(%s -> %s): %s",
                edge.relationship_type,
                src_type,
                tgt_type,
                edge,
            )
            continue

        valid.append(edge)
    return valid


def get_neo4j_driver() -> Driver:
    """Connect to Neo4j using configuration from docker-compose.yml.
    
    Returns:
        Connected Neo4j Driver instance.
        
    Raises:
        ConnectionError: If unable to connect after retries.
        
    Example:
        >>> driver = get_neo4j_driver()
        >>> driver.verify_connectivity()
    """
    logger.info(f"Connecting to Neo4j at {NEO4J_URI}...")
    
    for attempt in range(NEO4J_CONNECTION_RETRIES):
        try:
            driver = GraphDatabase.driver(
                NEO4J_URI,
                auth=(NEO4J_USERNAME, NEO4J_PASSWORD),
            )
            
            # Verify connectivity
            driver.verify_connectivity()
            
            logger.info(f"Connected to Neo4j at {NEO4J_URI}")
            return driver
            
        except Exception as e:
            logger.warning(
                f"Connection attempt {attempt + 1}/{NEO4J_CONNECTION_RETRIES} failed: {e}"
            )
            if attempt < NEO4J_CONNECTION_RETRIES - 1:
                time.sleep(NEO4J_CONNECTION_RETRY_DELAY)
            else:
                raise ConnectionError(
                    f"Failed to connect to Neo4j at {NEO4J_URI} "
                    f"after {NEO4J_CONNECTION_RETRIES} attempts"
                ) from e
    
    raise ConnectionError("Unexpected error in connection logic")


def init_graph_schema(driver: Driver) -> None:
    """Initialize Neo4j schema with constraints and indexes.
    
    Creates:
    - Unique constraint on Entity.global_uri
    - Indexes on entity_type, repo_name, file_path for filtering
    
    Args:
        driver: Connected Neo4j driver.
        
    Raises:
        Exception: If schema initialization fails.
    """
    with driver.session() as session:
        logger.info("Initializing Neo4j schema...")
        
        # Unique constraint on global_uri
        session.run(
            """
            CREATE CONSTRAINT entity_uri IF NOT EXISTS
            FOR (e:Entity) REQUIRE e.global_uri IS UNIQUE
            """
        )
        logger.debug("Created constraint: entity_uri")
        
        # Indexes for filtering
        indexes = [
            ("entity_type_idx", "entity_type"),
            ("entity_repo_idx", "repo_name"),
            ("entity_file_idx", "file_path"),
        ]
        
        for idx_name, field_name in indexes:
            session.run(
                f"""
                CREATE INDEX {idx_name} IF NOT EXISTS
                FOR (e:Entity) ON (e.{field_name})
                """
            )
            logger.debug(f"Created index: {idx_name}")
        
        logger.info("Neo4j schema initialized")


def clear_repo_graph(driver: Driver, repo_name: str) -> None:
    """Delete all nodes and edges for a specific repository.
    
    Args:
        driver: Connected Neo4j driver.
        repo_name: Repository to clear.
    """
    with driver.session() as session:
        logger.info(f"Clearing graph for repo: {repo_name}")
        
        result = session.run(
            """
            MATCH (e:Entity {repo_name: $repo_name})
            DETACH DELETE e
            RETURN count(e) AS deleted_count
            """,
            repo_name=repo_name,
        )
        
        count = result.single()["deleted_count"]
        logger.info(f"Deleted {count} nodes for repo {repo_name}")


def _build_nodes_from_symbols(
    symbols: list[ScipSymbolDef],
    repo_name: str,
) -> list[GraphNode]:
    """Convert SCIP symbol definitions to GraphNode objects.
    
    Applies smart namespace filtering via ``classify_symbol()``:
    - ``"drop"`` symbols are silently discarded.
    - ``"keep"`` symbols become full nodes (is_external=False).
    - ``"stub"`` symbols become stub nodes (is_external=True, file_path="<external>").
    
    Args:
        symbols: List of SCIP symbol definitions.
        repo_name: Repository name for URI generation.
        
    Returns:
        List of GraphNode objects (may be fewer than symbols due to filtering).
    """
    nodes: list[GraphNode] = []
    
    for sym in symbols:
        # classify_symbol already applied in parser, but symbols that
        # survived parsing are either "keep" or "stub" at definition site.
        # The symbol was defined in this repo's index, so it is "keep".
        
        global_uri = scip_symbol_to_global_uri(
            sym.scip_symbol,
            sym.file_path,
            repo_name,
            sym.kind,
        )
        
        if global_uri is None:
            continue
        
        try:
            parsed_uri = parse_global_uri(global_uri)
        except ValueError:
            logger.warning(f"Malformed global_uri: {global_uri}")
            continue
        
        nodes.append(
            GraphNode(
                global_uri=global_uri,
                repo_name=repo_name,
                file_path=sym.file_path,
                entity_type=parsed_uri["entity_type"],
                entity_name=parsed_uri["entity_name"],
                scip_symbol=sym.scip_symbol,
                is_external=False,  # Defined in this repo's SCIP index
            )
        )
    
    return nodes


def _build_edges_from_relationships(
    symbols: list[ScipSymbolDef],
    repo_name: str,
    stub_nodes: list[GraphNode],
    symbol_file_map: dict[str, str],
    symbol_kind_map: dict[str, int],
) -> list[GraphEdge]:
    """Extract edges from SCIP symbol relationships.
    
    Generates INHERITS, OVERRIDES edges from is_implementation relationships.
    
    **Cross-repo stub creation**: When a relationship target is classified
    as ``"stub"`` (monitored namespace but defined in another repo), a
    stub ``GraphNode`` is appended to ``stub_nodes`` so that Neo4j MERGE
    will create a placeholder that the sibling repo's ingestion will later
    complete.
    
    Args:
        symbols: List of SCIP symbol definitions with relationships.
        repo_name: Repository name for URI generation.
        stub_nodes: (mutated) Accumulates stub nodes for cross-repo targets.
        symbol_file_map: Lookup from SCIP symbol string to its definition
            file (``Document.relative_path``).  Used to resolve the correct
            file_path for relationship targets.
        symbol_kind_map: Lookup from SCIP symbol string to Kind enum.
        
    Returns:
        List of GraphEdge objects.
    """
    edges: list[GraphEdge] = []
    seen_stubs: set[str] = set()  # Deduplicate stubs
    
    for sym in symbols:
        src_uri = scip_symbol_to_global_uri(
            sym.scip_symbol,
            sym.file_path,
            repo_name,
            sym.kind,
        )
        
        if src_uri is None:
            continue
        
        for rel in sym.relationships:
            tgt_kind = symbol_kind_map.get(rel.target_symbol, 0)
            # Classify the target symbol
            tgt_disposition = classify_symbol(
                rel.target_symbol,
                kind=tgt_kind,
                is_local_definition=(rel.target_symbol in symbol_file_map),
            )
            
            if tgt_disposition == "drop":
                continue
            
            # Resolve the target's file_path from the symbol_file_map.
            # SCIP relationships do NOT carry the target's file path;
            # only the raw symbol string.  We must look it up.
            if tgt_disposition == "stub":
                tgt_file_path = "<external>"
            else:
                tgt_file_path = symbol_file_map.get(
                    rel.target_symbol, sym.file_path
                )
            
            tgt_uri = scip_symbol_to_global_uri(
                rel.target_symbol,
                tgt_file_path,
                repo_name,
                kind=tgt_kind,
            )
            
            if tgt_uri is None:
                continue
            
            # Create a stub node for cross-repo targets
            if tgt_disposition == "stub" and tgt_uri not in seen_stubs:
                seen_stubs.add(tgt_uri)
                
                parsed_tgt = parse_scip_symbol(rel.target_symbol, tgt_kind)
                if parsed_tgt:
                    stub_nodes.append(
                        GraphNode(
                            global_uri=tgt_uri,
                            repo_name=repo_name,
                            file_path="<external>",
                            entity_type=parsed_tgt.entity_type,
                            entity_name=parsed_tgt.entity_name,
                            scip_symbol=rel.target_symbol,
                            is_external=True,
                        )
                    )
            
            # Determine relationship type
            if rel.is_implementation:
                rel_type = _infer_implementation_edge_type(
                    src_symbol=sym.scip_symbol,
                    src_kind=sym.kind,
                    target_symbol=rel.target_symbol,
                    target_kind=tgt_kind,
                )
                if rel_type is not None:
                    edges.append(
                        GraphEdge(
                            src_uri=src_uri,
                            tgt_uri=tgt_uri,
                            relationship_type=rel_type,
                        )
                    )
            
            if rel.is_type_definition:
                parsed_src = parse_scip_symbol(sym.scip_symbol, sym.kind)
                parsed_tgt = parse_scip_symbol(rel.target_symbol, tgt_kind)
                if parsed_src is None or parsed_tgt is None:
                    continue
                if parsed_tgt.entity_type not in _TYPE_SYMBOLS:
                    continue
                if parsed_src.entity_type not in _IMPLEMENTATION_SRC_TYPES:
                    continue
                edges.append(
                    GraphEdge(
                        src_uri=src_uri,
                        tgt_uri=tgt_uri,
                        relationship_type="USES_TYPE",
                    )
                )
    
    return edges


def _build_edges_from_references(
    references: list[ScipReference],
    repo_name: str,
    stub_nodes: list[GraphNode],
    symbol_file_map: dict[str, str],
    symbol_kind_map: dict[str, int],
) -> list[GraphEdge]:
    """Extract CALLS edges from reference occurrences.
    
    When a reference to symbol B occurs inside the definition of symbol A,
    we infer A CALLS/USES B.
    
    **Cross-repo**: If the referenced symbol is classified as ``"stub"``,
    a stub node is created so the edge has something to land on.
    
    Args:
        references: List of SCIP reference occurrences.
        repo_name: Repository name for URI generation.
        stub_nodes: (mutated) Accumulates stub nodes for cross-repo targets.
        symbol_file_map: Lookup from SCIP symbol string to its definition
            file.  Used to resolve correct file_path for both the
            enclosing (source) and referenced (target) symbols.
        symbol_kind_map: Lookup from SCIP symbol string to Kind enum.
        
    Returns:
        List of GraphEdge objects for CALLS relationships.
    """
    edges: list[GraphEdge] = []
    seen_stubs: set[str] = {n.global_uri for n in stub_nodes}
    
    for ref in references:
        if ref.enclosing_symbol is None:
            continue
        
        # Resolve the enclosing symbol's file_path.
        # ref.file_path is the document where the *reference occurs*.
        # For the enclosing symbol this is usually correct (it is the
        # definition that surrounds the reference in the same document),
        # but we prefer the authoritative file from symbol_file_map
        # if available, for cases like inline methods from headers.
        enclosing_file = symbol_file_map.get(
            ref.enclosing_symbol, ref.file_path
        )
        src_uri = scip_symbol_to_global_uri(
            ref.enclosing_symbol,
            enclosing_file,
            repo_name,
            kind=symbol_kind_map.get(ref.enclosing_symbol, 0),
        )
        
        if src_uri is None:
            continue
        
        # Classify the target
        tgt_kind = symbol_kind_map.get(ref.scip_symbol, 0)
        tgt_disposition = classify_symbol(
            ref.scip_symbol,
            kind=tgt_kind,
            is_local_definition=(ref.scip_symbol in symbol_file_map),
        )
        if tgt_disposition == "drop":
            continue
        
        # Resolve the target's file_path from the symbol_file_map.
        # ref.file_path is where the reference *occurs*, NOT where
        # the target is defined — those are usually different files.
        if tgt_disposition == "stub":
            tgt_file = "<external>"
        else:
            tgt_file = symbol_file_map.get(ref.scip_symbol, ref.file_path)
        
        tgt_uri = scip_symbol_to_global_uri(
            ref.scip_symbol,
            tgt_file,
            repo_name,
            kind=tgt_kind,
        )
        
        if tgt_uri is None:
            continue
        
        # Create stub node for cross-repo targets
        if tgt_disposition == "stub" and tgt_uri not in seen_stubs:
            seen_stubs.add(tgt_uri)
            
            parsed_tgt = parse_scip_symbol(ref.scip_symbol, tgt_kind)
            if parsed_tgt:
                stub_nodes.append(
                    GraphNode(
                        global_uri=tgt_uri,
                        repo_name=repo_name,
                        file_path="<external>",
                        entity_type=parsed_tgt.entity_type,
                        entity_name=parsed_tgt.entity_name,
                        scip_symbol=ref.scip_symbol,
                        is_external=True,
                    )
                )
        
        parsed_src = parse_scip_symbol(
            ref.enclosing_symbol,
            kind=symbol_kind_map.get(ref.enclosing_symbol, 0),
        )
        parsed_tgt = parse_scip_symbol(ref.scip_symbol, kind=tgt_kind)
        if parsed_src is None or parsed_tgt is None:
            continue
        rel_type = _infer_reference_edge_type(
            src_entity_type=parsed_src.entity_type,
            tgt_entity_type=parsed_tgt.entity_type,
            role=ref.role,
        )
        if rel_type is None:
            continue
        
        edges.append(
            GraphEdge(
                src_uri=src_uri,
                tgt_uri=tgt_uri,
                relationship_type=rel_type,
            )
        )
    
    return edges


def ingest_graph(
    parse_result: ScipParseResult,
    driver: Driver,
    repo_name: str,
    batch_size: int = NEO4J_BATCH_SIZE,
) -> IngestionStats:
    """Ingest SCIP parse result into Neo4j graph database.
    
    Three-phase ingestion:
    1. MERGE nodes (batched)
    2. MERGE edges (batched by type)
    3. MERGE DEFINED_IN edges to File nodes
    
    Args:
        parse_result: Parsed SCIP index data.
        driver: Connected Neo4j driver.
        repo_name: Repository name.
        batch_size: Nodes/edges per batch.
        
    Returns:
        IngestionStats with metrics.
    """
    stats = IngestionStats()
    
    logger.info(f"Building graph data for repo: {repo_name}")
    
    # Build a lookup table: SCIP symbol string -> file where it is defined.
    # SCIP relationship and occurrence records only carry the target symbol
    # string — they do NOT carry the target's file path.  The correct file
    # path is only available from the Document.relative_path that contained
    # the symbol's SymbolInformation.  We build this map once and pass it
    # to the edge builders so they can resolve the target URI correctly.
    symbol_file_map: dict[str, str] = {
        sym.scip_symbol: sym.file_path
        for sym in parse_result.symbols
    }
    symbol_kind_map: dict[str, int] = {
        sym.scip_symbol: sym.kind
        for sym in parse_result.symbols
    }
    
    # Build nodes and edges (stub_nodes accumulates cross-repo placeholders)
    stub_nodes: list[GraphNode] = []
    nodes = _build_nodes_from_symbols(parse_result.symbols, repo_name)
    rel_edges = _build_edges_from_relationships(
        parse_result.symbols,
        repo_name,
        stub_nodes,
        symbol_file_map,
        symbol_kind_map,
    )
    ref_edges = _build_edges_from_references(
        parse_result.references,
        repo_name,
        stub_nodes,
        symbol_file_map,
        symbol_kind_map,
    )
    
    all_nodes_raw = nodes + stub_nodes
    all_edges_raw = rel_edges + ref_edges
    all_nodes = _dedupe_nodes(all_nodes_raw)
    deduped_edges = _dedupe_edges(all_edges_raw)
    all_edges = _validate_edges(deduped_edges)
    
    logger.info(
        f"Prepared {len(nodes)} local nodes + {len(stub_nodes)} stub nodes "
        f"(deduped to {len(all_nodes)}), "
        f"{len(all_edges)} edges "
        f"({len(rel_edges)} from relationships, {len(ref_edges)} from references)"
    )
    
    with driver.session() as session:
        # Phase 1: MERGE nodes (batched by entity_type for labeling)
        # all_nodes includes both local nodes and cross-repo stub nodes
        logger.info("Phase 1: Merging nodes...")
        
        nodes_by_type: dict[str, list[GraphNode]] = {}
        for node in all_nodes:
            nodes_by_type.setdefault(node.entity_type, []).append(node)
        
        for entity_type, type_nodes in nodes_by_type.items():
            for i in range(0, len(type_nodes), batch_size):
                batch = type_nodes[i : i + batch_size]
                
                node_dicts = [
                    {
                        "global_uri": n.global_uri,
                        "repo_name": n.repo_name,
                        "file_path": n.file_path,
                        "entity_type": n.entity_type,
                        "entity_name": n.entity_name,
                        "scip_symbol": n.scip_symbol,
                        "is_external": n.is_external,
                    }
                    for n in batch
                ]
                
                # Use entity-type-specific labels (no APOC dependency)
                result = session.run(
                    f"""
                    UNWIND $nodes AS n
                    MERGE (e:Entity:{entity_type} {{global_uri: n.global_uri}})
                    SET e.repo_name = n.repo_name,
                        e.file_path = n.file_path,
                        e.entity_type = n.entity_type,
                        e.entity_name = n.entity_name,
                        e.scip_symbol = n.scip_symbol,
                        e.is_external = n.is_external
                    RETURN count(e) AS count
                    """,
                    nodes=node_dicts,
                )
                
                count = result.single()["count"]
                stats.nodes_created += count
                stats.batches_sent += 1
                
                logger.debug(
                    f"Merged {count} {entity_type} nodes "
                    f"(total: {stats.nodes_created})"
                )
        
        logger.info(f"Phase 1 complete: {stats.nodes_created} nodes merged")
        
        # Phase 2: MERGE edges (batched by relationship_type)
        logger.info("Phase 2: Merging edges...")
        
        edges_by_type: dict[str, list[GraphEdge]] = {}
        for edge in all_edges:
            edges_by_type.setdefault(edge.relationship_type, []).append(edge)
        
        for rel_type, type_edges in edges_by_type.items():
            for i in range(0, len(type_edges), batch_size):
                batch = type_edges[i : i + batch_size]
                
                edge_dicts = [
                    {"src_uri": e.src_uri, "tgt_uri": e.tgt_uri}
                    for e in batch
                ]
                
                try:
                    result = session.run(
                        f"""
                        UNWIND $edges AS e
                        MATCH (src:Entity {{global_uri: e.src_uri}})
                        MATCH (tgt:Entity {{global_uri: e.tgt_uri}})
                        MERGE (src)-[r:{rel_type}]->(tgt)
                        RETURN count(r) AS count
                        """,
                        edges=edge_dicts,
                    )
                    
                    count = result.single()["count"]
                    stats.edges_created += count
                    stats.batches_sent += 1
                    
                    logger.debug(
                        f"Merged {count} {rel_type} edges "
                        f"(total: {stats.edges_created})"
                    )
                    
                except Exception as e:
                    logger.error(f"Failed to merge {rel_type} edges: {e}")
                    stats.errors += len(batch)
        
        logger.info(f"Phase 2 complete: {stats.edges_created} edges merged")
        
        # Phase 3: DEFINED_IN edges (group entities by file)
        logger.info("Phase 3: Creating DEFINED_IN file edges...")
        
        entities_by_file: dict[str, list[str]] = {}
        for node in all_nodes:
            # Skip stubs from DEFINED_IN — they don't have a real file
            if node.file_path == "<external>":
                continue
            entities_by_file.setdefault(node.file_path, []).append(node.global_uri)
        
        file_dicts = [
            {"file_path": fp, "repo_name": repo_name, "entities": uris}
            for fp, uris in entities_by_file.items()
        ]
        
        for i in range(0, len(file_dicts), batch_size):
            batch = file_dicts[i : i + batch_size]
            
            session.run(
                """
                UNWIND $files AS f
                MERGE (file:File {path: f.file_path, repo_name: f.repo_name})
                WITH file, f
                UNWIND f.entities AS uri
                MATCH (e:Entity {global_uri: uri})
                MERGE (e)-[:DEFINED_IN]->(file)
                """,
                files=batch,
            )
            
            stats.batches_sent += 1
        
        logger.info(f"Phase 3 complete: DEFINED_IN edges created for {len(entities_by_file)} files")
    
    logger.info(f"Graph ingestion complete: {stats}")
    return stats


def main() -> None:
    """Manual verification of Neo4j connection and schema."""
    
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    
    logger.info("=" * 80)
    logger.info(" Neo4j Loader - Manual Verification")
    logger.info("=" * 80)
    
    try:
        driver = get_neo4j_driver()
        init_graph_schema(driver)
        
        # Test query
        with driver.session() as session:
            result = session.run("RETURN 1 AS test")
            assert result.single()["test"] == 1
        
        logger.info("Manual verification successful")
        driver.close()
        
    except Exception as e:
        logger.error(f"Manual verification failed: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    main()
