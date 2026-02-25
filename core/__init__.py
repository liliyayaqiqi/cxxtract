"""Core shared contracts and utilities."""

from core.uri_contract import (
    GLOBAL_URI_SEPARATOR,
    build_identity_key,
    create_global_uri,
    make_function_signature_hash,
    normalize_cpp_entity_name,
    parse_global_uri,
)
from core.structured_logging import (
    configure_structured_logging,
    get_run_id,
    phase_scope,
    set_run_id,
)
from core.startup_config import (
    ConfigValidationError,
    load_docker_compose_config,
    resolve_neo4j_auth,
    resolve_service_port,
    resolve_strict_config_validation,
    validate_startup_config,
)
from core.run_artifacts import write_run_report
from core.workspace_manifest import (
    CompdbSpec,
    RepoSpec,
    WorkspaceManifest,
    load_workspace_manifest,
    resolve_compdb_path,
)
from core.git_source import (
    RepoCheckoutResult,
    checkout_ref,
    sync_repo,
)

__all__ = [
    "GLOBAL_URI_SEPARATOR",
    "build_identity_key",
    "create_global_uri",
    "make_function_signature_hash",
    "normalize_cpp_entity_name",
    "parse_global_uri",
    "configure_structured_logging",
    "get_run_id",
    "phase_scope",
    "set_run_id",
    "ConfigValidationError",
    "load_docker_compose_config",
    "resolve_neo4j_auth",
    "resolve_service_port",
    "resolve_strict_config_validation",
    "validate_startup_config",
    "write_run_report",
    "CompdbSpec",
    "RepoSpec",
    "WorkspaceManifest",
    "load_workspace_manifest",
    "resolve_compdb_path",
    "RepoCheckoutResult",
    "checkout_ref",
    "sync_repo",
]
