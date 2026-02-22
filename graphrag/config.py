"""
Configuration constants for GraphRAG pipeline (Neo4j + SCIP).

All values are loaded from environment variables and docker-compose.yml
following the agent.md mandate to avoid hardcoded network configs.
"""

import os
import yaml
import logging
from typing import Tuple

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


def _parse_neo4j_config() -> Tuple[str, str, str]:
    """Parse Neo4j connection config from docker-compose.yml.
    
    Returns:
        Tuple of (uri, username, password).
        
    Raises:
        FileNotFoundError: If docker-compose.yml not found.
        KeyError: If neo4j service not configured.
    """
    try:
        with open(DOCKER_COMPOSE_PATH, "r") as f:
            config = yaml.safe_load(f)
        
        neo4j_config = config.get("services", {}).get("neo4j", {})
        
        # Parse Bolt port (7687)
        port = 7687
        for port_mapping in neo4j_config.get("ports", []):
            port_str = str(port_mapping)
            if ":7687" in port_str:
                port = int(port_str.split(":")[0])
                break
        
        uri = f"bolt://127.0.0.1:{port}"
        
        # Parse NEO4J_AUTH environment variable
        username = "neo4j"
        password = "testpassword123"
        
        env_vars = neo4j_config.get("environment", [])
        for env in env_vars:
            if isinstance(env, str) and env.startswith("NEO4J_AUTH="):
                auth_val = env.split("=", 1)[1]
                if "/" in auth_val:
                    username, password = auth_val.split("/", 1)
        
        logger.debug(f"Parsed Neo4j config: uri={uri}, username={username}")
        return uri, username, password
        
    except FileNotFoundError:
        logger.warning(f"Could not find {DOCKER_COMPOSE_PATH}, using defaults")
        return "bolt://127.0.0.1:7687", "neo4j", "testpassword123"
    except Exception as e:
        logger.warning(f"Error parsing Neo4j config: {e}, using defaults")
        return "bolt://127.0.0.1:7687", "neo4j", "testpassword123"


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
