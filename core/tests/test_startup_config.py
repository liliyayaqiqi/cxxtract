"""Tests for startup config validation helpers."""

import tempfile
import unittest
from pathlib import Path

from core.startup_config import (
    ConfigValidationError,
    load_docker_compose_config,
    resolve_neo4j_auth,
    resolve_service_port,
    validate_startup_config,
)


class TestStartupConfig(unittest.TestCase):
    def _write_compose(self, content: str) -> str:
        handle = tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False)
        handle.write(content)
        handle.flush()
        handle.close()
        return handle.name

    def test_load_non_strict_missing_returns_empty(self) -> None:
        payload = load_docker_compose_config("/definitely/missing.yml", strict=False)
        self.assertEqual(payload, {})

    def test_load_strict_missing_raises(self) -> None:
        with self.assertRaises(ConfigValidationError):
            load_docker_compose_config("/definitely/missing.yml", strict=True)

    def test_resolve_service_port_parses_mapping(self) -> None:
        compose = {
            "services": {
                "qdrant": {
                    "ports": ["127.0.0.1:6339:6333", "6334:6334"],
                }
            }
        }
        port = resolve_service_port(
            compose_data=compose,
            service_name="qdrant",
            container_port=6333,
            default_port=6333,
            strict=True,
        )
        self.assertEqual(port, 6339)

    def test_resolve_neo4j_auth_strict_invalid_raises(self) -> None:
        compose = {"services": {"neo4j": {"environment": ["NEO4J_AUTH=invalid"]}}}
        with self.assertRaises(ConfigValidationError):
            resolve_neo4j_auth(compose_data=compose, strict=True)

    def test_validate_startup_config_strict_missing_service_raises(self) -> None:
        path = self._write_compose("services:\n  qdrant:\n    ports: ['6333:6333']\n")
        try:
            with self.assertRaises(ConfigValidationError):
                validate_startup_config(
                    compose_path=path,
                    required_services=("qdrant", "neo4j"),
                    strict=True,
                )
        finally:
            Path(path).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
