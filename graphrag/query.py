"""
Graph query functions for dependency analysis and blast radius calculation.

Provides high-level query APIs that will be exposed via MCP in Layer 3.
Uses Neo4j's index-free adjacency for O(1) traversal performance.
"""

import logging
from dataclasses import dataclass, field
from typing import Literal

from neo4j import Driver

logger = logging.getLogger(__name__)


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


def calculate_blast_radius(
    global_uri: str,
    driver: Driver,
    max_depth: int = 5,
    direction: Literal["upstream", "downstream"] = "upstream",
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
    
    result = BlastRadiusResult(
        origin_uri=global_uri,
        direction=direction,
    )
    
    # Determine Cypher pattern based on direction
    if direction == "upstream":
        # Inbound edges — what depends on me
        pattern = "(start)<-[:CALLS|USES_TYPE|INHERITS|OVERRIDES*1..%d]-(affected)"
    else:
        # Outbound edges — what I depend on
        pattern = "(start)-[:CALLS|USES_TYPE|INHERITS|OVERRIDES*1..%d]->(affected)"
    
    pattern = pattern % max_depth
    
    query = f"""
    MATCH (start:Entity {{global_uri: $uri}})
    OPTIONAL MATCH path = {pattern}
    WITH affected, path
    WHERE affected IS NOT NULL
    RETURN DISTINCT
        affected.global_uri AS uri,
        affected.entity_type AS type,
        affected.entity_name AS name,
        affected.file_path AS file,
        length(path) AS depth,
        [rel IN relationships(path) | type(rel)] AS chain
    ORDER BY depth ASC
    """
    
    with driver.session() as session:
        records = session.run(query, uri=global_uri)
        
        for record in records:
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
    
    logger.info(
        f"Blast radius: {result.total_count} affected entities, "
        f"max depth {result.max_depth_reached}"
    )
    
    return result


def get_entity_neighbors(
    global_uri: str,
    driver: Driver,
) -> dict[str, list[dict]]:
    """Get immediate neighbors (1-hop) of an entity.
    
    Args:
        global_uri: The Global URI to query.
        driver: Connected Neo4j driver.
        
    Returns:
        Dict with keys "inbound" and "outbound", each containing a list of
        neighbor dicts with "uri", "type", "relationship" fields.
    """
    with driver.session() as session:
        # Inbound (what depends on me)
        inbound_query = """
        MATCH (src)-[r]->(tgt:Entity {global_uri: $uri})
        RETURN src.global_uri AS uri, src.entity_type AS type, type(r) AS relationship
        """
        
        inbound = [
            {
                "uri": rec["uri"],
                "type": rec["type"],
                "relationship": rec["relationship"],
            }
            for rec in session.run(inbound_query, uri=global_uri)
        ]
        
        # Outbound (what I depend on)
        outbound_query = """
        MATCH (src:Entity {global_uri: $uri})-[r]->(tgt)
        RETURN tgt.global_uri AS uri, tgt.entity_type AS type, type(r) AS relationship
        """
        
        outbound = [
            {
                "uri": rec["uri"],
                "type": rec["type"],
                "relationship": rec["relationship"],
            }
            for rec in session.run(outbound_query, uri=global_uri)
        ]
    
    return {"inbound": inbound, "outbound": outbound}


def get_inheritance_tree(
    global_uri: str,
    driver: Driver,
) -> dict[str, list[str]]:
    """Get the full inheritance hierarchy for a class.
    
    Args:
        global_uri: The Global URI of a Class/Struct entity.
        driver: Connected Neo4j driver.
        
    Returns:
        Dict with keys "ancestors" (base classes) and "descendants" (derived classes).
    """
    with driver.session() as session:
        # Ancestors (base classes)
        ancestors_query = """
        MATCH path = (child:Entity {global_uri: $uri})-[:INHERITS*]->(ancestor)
        RETURN ancestor.global_uri AS uri
        ORDER BY length(path) ASC
        """
        
        ancestors = [
            rec["uri"] for rec in session.run(ancestors_query, uri=global_uri)
        ]
        
        # Descendants (derived classes)
        descendants_query = """
        MATCH path = (descendant)-[:INHERITS*]->(base:Entity {global_uri: $uri})
        RETURN descendant.global_uri AS uri
        ORDER BY length(path) ASC
        """
        
        descendants = [
            rec["uri"] for rec in session.run(descendants_query, uri=global_uri)
        ]
    
    return {"ancestors": ancestors, "descendants": descendants}
