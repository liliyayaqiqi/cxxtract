import logging
import yaml
import sys
import os
from typing import Dict, Any, Tuple
from qdrant_client import QdrantClient
from neo4j import GraphDatabase

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def load_config(file_path: str) -> Dict[str, Any]:
    """Loads and returns the docker-compose configuration.

    Args:
        file_path: Path to the docker-compose.yml file.

    Returns:
        Dictionary containing the parsed YAML configuration.

    Raises:
        FileNotFoundError: If the file does not exist.
        yaml.YAMLError: If parsing fails.
    """
    try:
        with open(file_path, 'r') as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        logger.error(f"Configuration file not found: {file_path}")
        raise
    except yaml.YAMLError as e:
        logger.error(f"Error parsing YAML: {e}")
        raise

def get_service_config(compose_data: Dict[str, Any], service_name: str) -> Dict[str, Any]:
    """Retrieves configuration for a specific service.

    Args:
        compose_data: The full docker-compose configuration.
        service_name: Name of the service to retrieve.

    Returns:
        The service configuration dictionary.

    Raises:
        KeyError: If the service is not found.
    """
    try:
        return compose_data['services'][service_name]
    except KeyError:
        logger.error(f"Service '{service_name}' not found in configuration.")
        raise

def parse_port(port_mapping: str) -> int:
    """Parses a docker-compose port mapping string (e.g., '6333:6333').

    Args:
        port_mapping: The port string.

    Returns:
        The host port as an integer.
    """
    # Handles "HOST:CONTAINER" format
    return int(str(port_mapping).split(':')[0])

import time

def check_qdrant_connection(host: str, port: int, retries: int = 3) -> bool:
    """Verifies connection to Qdrant instance.

    Args:
        host: Hostname or IP.
        port: Port number.
        retries: Number of connection attempts.

    Returns:
        True if connected, False otherwise.
    """
    logger.info(f"Testing Qdrant connection at {host}:{port}...")
    for attempt in range(retries):
        try:
            # QdrantClient tries to connect immediately upon instantiation if url/host is provided? 
            # Actually it's lazy unless operations are performed, but let's try a light operation.
            client = QdrantClient(host=host, port=port)
            collections = client.get_collections()
            logger.info(f"✅ Qdrant Connection Successful! Found {len(collections.collections)} collections.")
            return True
        except Exception as e:
            logger.warning(f"Attempt {attempt + 1}/{retries} failed: {e}")
            if attempt < retries - 1:
                time.sleep(2)
            else:
                logger.error(f"❌ Qdrant Connection Failed after {retries} attempts: {e}")
                return False
    return False

def check_neo4j_connection(uri: str, auth: Tuple[str, str], retries: int = 3) -> bool:
    """Verifies connection to Neo4j instance.

    Args:
        uri: Connection URI (e.g., bolt://localhost:7687).
        auth: Tuple of (username, password).
        retries: Number of connection attempts.

    Returns:
        True if connected, False otherwise.
    """
    logger.info(f"Testing Neo4j connection at {uri}...")
    for attempt in range(retries):
        driver = None
        try:
            driver = GraphDatabase.driver(uri, auth=auth)
            driver.verify_connectivity()
            logger.info("✅ Neo4j Connection Successful!")
            return True
        except Exception as e:
            logger.warning(f"Attempt {attempt + 1}/{retries} failed: {e}")
            if attempt < retries - 1:
                time.sleep(2)
            else:
                logger.error(f"❌ Neo4j Connection Failed after {retries} attempts: {e}")
                return False
        finally:
            if driver:
                driver.close()
    return False

def main() -> None:
    """Main execution function."""
    config_path = 'infra_context/docker-compose.yml'
    
    try:
        logger.info(f"Parsing configuration from {config_path}...")
        config = load_config(config_path)
        
        # Qdrant Setup
        q_config = get_service_config(config, 'qdrant')
        # By default Qdrant HTTP is 6333. We look for it.
        q_port = 6333 
        for p in q_config.get('ports', []):
            host_p = parse_port(p)
            # Heuristic: 6333 is the standard HTTP port we want
            if host_p == 6333 or str(p).endswith(':6333'):
                q_port = host_p
                break
        
        # Neo4j Setup
        n_config = get_service_config(config, 'neo4j')
        # Find the Bolt port (7687)
        n_port = 7687
        for p in n_config.get('ports', []):
            host_p = parse_port(p)
            if host_p == 7687 or str(p).endswith(':7687'):
                n_port = host_p
                break
        
        # Parse Auth
        username = 'neo4j' # Default
        password = ''
        
        env_vars = n_config.get('environment', [])
        for env in env_vars:
            if isinstance(env, str) and env.startswith('NEO4J_AUTH='):
                auth_val = env.split('=', 1)[1]
                if '/' in auth_val:
                    username, password = auth_val.split('/', 1)
                else:
                    # Handle cases like "none" or just password? Docker image usually expects user/pass
                    pass
        
        if not password:
             logger.warning("Could not explicitly parse password from NEO4J_AUTH. Using defaults if valid.")

        # Execute Checks
        # Use 127.0.0.1 instead of localhost to avoid IPv6 resolution issues with OrbStack/Docker
        q_status = check_qdrant_connection('127.0.0.1', q_port)
        n_status = check_neo4j_connection(f"bolt://127.0.0.1:{n_port}", (username, password))

        if q_status and n_status:
            logger.info("🎉 All infrastructure connections verified successfully.")
            sys.exit(0)
        else:
            logger.error("🔥 One or more connection checks failed.")
            sys.exit(1)
            
    except Exception as e:
        logger.critical(f"Infrastructure verification failed with unhandled error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
