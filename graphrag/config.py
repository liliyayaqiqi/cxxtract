"""
Configuration constants for GraphRAG pipeline (Neo4j + SCIP).

All values are loaded from environment variables and docker-compose.yml
following the agent.md mandate to avoid hardcoded network configs.
"""

import os
import logging
from typing import Tuple

from core.startup_config import (
    load_docker_compose_config,
    resolve_neo4j_auth,
    resolve_service_port,
    resolve_strict_config_validation,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Infrastructure paths
# ---------------------------------------------------------------------------
DOCKER_COMPOSE_PATH: str = "infra_context/docker-compose.yml"

# ---------------------------------------------------------------------------
# SCIP configuration
# ---------------------------------------------------------------------------
SCIP_CLANG_PATH: str = os.getenv("SCIP_CLANG_PATH", "scip-clang")
DEFAULT_INDEX_OUTPUT: str = "output/index.scip"

# ---------------------------------------------------------------------------
# Neo4j configuration (parsed from docker-compose.yml)
# ---------------------------------------------------------------------------

STRICT_CONFIG_VALIDATION: bool = resolve_strict_config_validation(default=False)


def _parse_neo4j_config() -> Tuple[str, str, str]:
    """Parse Neo4j connection config from docker-compose.yml.
    
    Returns:
        Tuple of (uri, username, password).
        
    Raises:
        FileNotFoundError: If docker-compose.yml not found.
        KeyError: If neo4j service not configured.
    """
    compose = load_docker_compose_config(
        DOCKER_COMPOSE_PATH,
        strict=STRICT_CONFIG_VALIDATION,
    )
    port = resolve_service_port(
        compose_data=compose,
        service_name="neo4j",
        container_port=7687,
        default_port=7687,
        strict=STRICT_CONFIG_VALIDATION,
    )
    username, password = resolve_neo4j_auth(
        compose_data=compose,
        default_username="neo4j",
        default_password="testpassword123",
        strict=STRICT_CONFIG_VALIDATION,
    )

    uri = f"bolt://127.0.0.1:{port}"
    logger.debug("Parsed Neo4j config: uri=%s, username=%s", uri, username)
    return uri, username, password


NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD = _parse_neo4j_config()

# ---------------------------------------------------------------------------
# Neo4j ingestion configuration
# ---------------------------------------------------------------------------
NEO4J_BATCH_SIZE: int = 500
NEO4J_CONNECTION_RETRIES: int = 3
NEO4J_CONNECTION_RETRY_DELAY: float = 2.0  # seconds

# ---------------------------------------------------------------------------
# Symbol Namespace Filtering (Smart Dependency Filtering)
# ---------------------------------------------------------------------------
# These lists govern what flows into Neo4j. The logic is:
#
#   1. If first_namespace ∈ IGNORED_NAMESPACES → DROP (never ingest).
#   2. If first_namespace ∈ MONITORED_NAMESPACES → KEEP (always ingest,
#      as a stub node if the definition lives in another repo).
#   3. Everything else → KEEP if it is defined inside the current SCIP index.
#
# This prevents millions of std::/ __gnu_cxx:: nodes from exploding the
# graph while still allowing cross-repo linkage between sibling projects.
# ---------------------------------------------------------------------------

IGNORED_NAMESPACES: list[str] = [
    "std",
    "__gnu_cxx",
    "__cxxabiv1",
    "__gnu_debug",
    "boost",
    "__sanitizer",
    "__asan",
]
"""Top-level C++ namespaces whose symbols are unconditionally dropped.

These are system/third-party library namespaces that produce massive node
counts with no blast-radius value.  Configurable per deployment.
"""

MONITORED_NAMESPACES: list[str] = [
    "YAML",
    "webrtc",
    "rtc",
    "game_hook",
    "common",
    "models",
]
"""Top-level namespaces belonging to your organisation's repositories.

Symbols in these namespaces are **always kept**, even when the definition
is not in the current SCIP index (cross-repo stub).  When the sibling
repo is later indexed, Neo4j's MERGE will complete the stub node.
"""
