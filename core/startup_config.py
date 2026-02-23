"""Startup configuration validation helpers.

Provides strict/non-strict docker-compose parsing used by runtime startup
checks and database connection bootstrap code.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)


class ConfigValidationError(RuntimeError):
    """Raised when strict startup validation fails."""


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def resolve_strict_config_validation(default: bool = False) -> bool:
    """Resolve strict validation mode from ``STRICT_CONFIG_VALIDATION`` env."""
    return _env_flag("STRICT_CONFIG_VALIDATION", default=default)


def load_docker_compose_config(
    compose_path: str,
    strict: bool = False,
) -> dict[str, Any]:
    """Load and parse docker-compose config.

    In non-strict mode this returns an empty dict on parse/read failures.
    In strict mode this raises ``ConfigValidationError``.
    """
    try:
        with open(compose_path, "r", encoding="utf-8") as f:
            payload = yaml.safe_load(f)
    except FileNotFoundError as exc:
        msg = f"Docker compose file not found: {compose_path}"
        if strict:
            raise ConfigValidationError(msg) from exc
        logger.warning("%s; continuing with defaults", msg)
        return {}
    except yaml.YAMLError as exc:
        msg = f"Failed to parse docker compose YAML at {compose_path}: {exc}"
        if strict:
            raise ConfigValidationError(msg) from exc
        logger.warning("%s; continuing with defaults", msg)
        return {}

    if payload is None:
        msg = f"Docker compose file is empty: {compose_path}"
        if strict:
            raise ConfigValidationError(msg)
        logger.warning("%s; continuing with defaults", msg)
        return {}

    if not isinstance(payload, dict):
        msg = f"Unexpected docker compose payload type: {type(payload).__name__}"
        if strict:
            raise ConfigValidationError(msg)
        logger.warning("%s; continuing with defaults", msg)
        return {}

    return payload


def get_service_config(
    compose_data: dict[str, Any],
    service_name: str,
    strict: bool = False,
) -> dict[str, Any]:
    """Fetch service config from compose payload."""
    services = compose_data.get("services")
    if not isinstance(services, dict):
        msg = "docker-compose missing 'services' section"
        if strict:
            raise ConfigValidationError(msg)
        logger.warning("%s; using defaults", msg)
        return {}

    service = services.get(service_name)
    if not isinstance(service, dict):
        msg = f"docker-compose missing service '{service_name}'"
        if strict:
            raise ConfigValidationError(msg)
        logger.warning("%s; using defaults", msg)
        return {}
    return service


def _parse_port_mapping(mapping: Any) -> Optional[tuple[int, int]]:
    """Parse host/container port mapping from compose ``ports`` entry."""
    text = str(mapping).strip().strip('"').strip("'")
    if not text:
        return None

    if "/" in text:
        text = text.split("/", 1)[0]

    if ":" not in text:
        try:
            port = int(text)
        except ValueError:
            return None
        return port, port

    parts = text.split(":")
    if len(parts) < 2:
        return None

    host_port_raw = parts[-2]
    container_port_raw = parts[-1]
    try:
        host_port = int(host_port_raw)
        container_port = int(container_port_raw)
    except ValueError:
        return None
    return host_port, container_port


def resolve_service_port(
    compose_data: dict[str, Any],
    service_name: str,
    container_port: int,
    default_port: int,
    strict: bool = False,
) -> int:
    """Resolve mapped host port for ``service_name:container_port``."""
    service = get_service_config(compose_data, service_name, strict=strict)
    ports = service.get("ports", [])
    if not isinstance(ports, list):
        msg = f"Service '{service_name}' has invalid 'ports' section"
        if strict:
            raise ConfigValidationError(msg)
        logger.warning("%s; using default %d", msg, default_port)
        return default_port

    for mapping in ports:
        parsed = _parse_port_mapping(mapping)
        if parsed is None:
            continue
        host, container = parsed
        if container == container_port:
            return host

    msg = (
        f"Service '{service_name}' has no mapping for container port "
        f"{container_port}"
    )
    if strict:
        raise ConfigValidationError(msg)
    logger.warning("%s; using default %d", msg, default_port)
    return default_port


def resolve_neo4j_auth(
    compose_data: dict[str, Any],
    default_username: str = "neo4j",
    default_password: str = "testpassword123",
    strict: bool = False,
) -> tuple[str, str]:
    """Resolve Neo4j auth from docker-compose ``NEO4J_AUTH`` setting."""
    service = get_service_config(compose_data, "neo4j", strict=strict)
    env_items = service.get("environment", [])

    entries: list[str] = []
    if isinstance(env_items, list):
        entries = [str(item) for item in env_items]
    elif isinstance(env_items, dict):
        entries = [f"{k}={v}" for k, v in env_items.items()]
    elif env_items:
        msg = "neo4j.environment must be list or dict"
        if strict:
            raise ConfigValidationError(msg)
        logger.warning("%s; using default auth", msg)
        return default_username, default_password

    for entry in entries:
        if not entry.startswith("NEO4J_AUTH="):
            continue
        raw = entry.split("=", 1)[1]
        if "/" not in raw:
            msg = "NEO4J_AUTH must be '<username>/<password>'"
            if strict:
                raise ConfigValidationError(msg)
            logger.warning("%s; using default auth", msg)
            return default_username, default_password
        username, password = raw.split("/", 1)
        if not username or not password:
            msg = "NEO4J_AUTH contains empty username or password"
            if strict:
                raise ConfigValidationError(msg)
            logger.warning("%s; using default auth", msg)
            return default_username, default_password
        return username, password

    msg = "NEO4J_AUTH not found in neo4j service environment"
    if strict:
        raise ConfigValidationError(msg)
    logger.warning("%s; using default auth", msg)
    return default_username, default_password


def validate_startup_config(
    compose_path: str,
    required_services: tuple[str, ...],
    strict: bool = False,
) -> dict[str, Any]:
    """Validate startup configuration and return a summary."""
    compose_data = load_docker_compose_config(compose_path, strict=strict)
    services = compose_data.get("services")
    if not isinstance(services, dict):
        services = {}

    missing: list[str] = []
    for service in required_services:
        if service not in services:
            missing.append(service)

    if missing and strict:
        raise ConfigValidationError(
            "Missing required services in docker-compose: " + ", ".join(missing)
        )

    if missing:
        logger.warning(
            "Missing services (%s) in docker-compose; defaults may be used",
            ", ".join(missing),
        )

    return {
        "compose_path": compose_path,
        "strict": strict,
        "required_services": list(required_services),
        "missing_services": missing,
    }
