"""Manifest contract for workspace indexing pipeline."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class CompdbSpec:
    """Compile database input descriptor."""

    path: str


@dataclass(frozen=True)
class RepoSpec:
    """Repository configuration for workspace pipeline."""

    repo_name: str
    git_url: str
    ref: str
    token_env: str
    compdb_paths: list[CompdbSpec]
    source_subdir: str = "."
    enabled: bool = True
    run_vector: bool = True
    run_graph: bool = True


@dataclass(frozen=True)
class QdrantWorkspaceConfig:
    """Workspace-level Qdrant controls."""

    recreate_collection: bool = False
    collection_name: str | None = None


@dataclass(frozen=True)
class Neo4jWorkspaceConfig:
    """Workspace-level Neo4j controls."""

    recreate_graph: bool = False


@dataclass(frozen=True)
class WorkspaceManifest:
    """Top-level manifest payload."""

    workspace_name: str
    repos: list[RepoSpec]
    repo_cache_dir: str = "output/workspace_repos"
    index_dir: str = "output/workspace_scip"
    entities_dir: str = "output/workspace_entities"
    qdrant: QdrantWorkspaceConfig = field(default_factory=QdrantWorkspaceConfig)
    neo4j: Neo4jWorkspaceConfig = field(default_factory=Neo4jWorkspaceConfig)


def _expect_dict(payload: Any, ctx: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError(f"{ctx} must be an object")
    return payload


def _load_manifest_payload(path: str) -> dict[str, Any]:
    manifest_path = Path(path)
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Manifest file not found: {manifest_path}")

    text = manifest_path.read_text(encoding="utf-8")
    suffix = manifest_path.suffix.lower()
    if suffix == ".json":
        payload = json.loads(text)
    else:
        payload = yaml.safe_load(text)
    return _expect_dict(payload, "manifest")


def _parse_repo_spec(repo_payload: dict[str, Any]) -> RepoSpec:
    repo_name = str(repo_payload.get("repo_name", "")).strip()
    git_url = str(repo_payload.get("git_url", "")).strip()
    ref = str(repo_payload.get("ref", "")).strip()
    token_env = str(repo_payload.get("token_env", "")).strip()
    source_subdir = str(repo_payload.get("source_subdir", ".")).strip() or "."
    enabled = bool(repo_payload.get("enabled", True))
    run_vector = bool(repo_payload.get("run_vector", True))
    run_graph = bool(repo_payload.get("run_graph", True))

    if not repo_name:
        raise ValueError("repo.repo_name is required")
    if not git_url:
        raise ValueError(f"repo '{repo_name}': git_url is required")
    if not ref:
        raise ValueError(f"repo '{repo_name}': ref is required")
    if not token_env:
        raise ValueError(f"repo '{repo_name}': token_env is required")

    compdb_raw = repo_payload.get("compdb_paths")
    if not isinstance(compdb_raw, list) or len(compdb_raw) == 0:
        raise ValueError(f"repo '{repo_name}': compdb_paths must be a non-empty list")

    compdb_paths: list[CompdbSpec] = []
    for item in compdb_raw:
        path = str(item).strip()
        if not path:
            raise ValueError(f"repo '{repo_name}': compdb_paths contains empty path")
        compdb_paths.append(CompdbSpec(path=path))

    return RepoSpec(
        repo_name=repo_name,
        git_url=git_url,
        ref=ref,
        token_env=token_env,
        compdb_paths=compdb_paths,
        source_subdir=source_subdir,
        enabled=enabled,
        run_vector=run_vector,
        run_graph=run_graph,
    )


def load_workspace_manifest(path: str) -> WorkspaceManifest:
    """Load and validate workspace manifest from YAML/JSON file."""
    payload = _load_manifest_payload(path)
    workspace_name = str(payload.get("workspace_name", "")).strip()
    if not workspace_name:
        raise ValueError("workspace_name is required")

    repos_raw = payload.get("repos")
    if not isinstance(repos_raw, list) or len(repos_raw) == 0:
        raise ValueError("repos must be a non-empty list")

    repos: list[RepoSpec] = []
    seen: set[str] = set()
    for raw in repos_raw:
        repo_payload = _expect_dict(raw, "repo entry")
        spec = _parse_repo_spec(repo_payload)
        if spec.repo_name in seen:
            raise ValueError(f"Duplicate repo_name in manifest: {spec.repo_name}")
        seen.add(spec.repo_name)
        repos.append(spec)

    qdrant_payload = _expect_dict(payload.get("qdrant", {}), "qdrant")
    neo4j_payload = _expect_dict(payload.get("neo4j", {}), "neo4j")

    return WorkspaceManifest(
        workspace_name=workspace_name,
        repos=repos,
        repo_cache_dir=str(payload.get("repo_cache_dir", "output/workspace_repos")),
        index_dir=str(payload.get("index_dir", "output/workspace_scip")),
        entities_dir=str(payload.get("entities_dir", "output/workspace_entities")),
        qdrant=QdrantWorkspaceConfig(
            recreate_collection=bool(qdrant_payload.get("recreate_collection", False)),
            collection_name=(
                str(qdrant_payload["collection_name"])
                if qdrant_payload.get("collection_name") is not None
                else None
            ),
        ),
        neo4j=Neo4jWorkspaceConfig(
            recreate_graph=bool(neo4j_payload.get("recreate_graph", False)),
        ),
    )


def resolve_compdb_path(repo_checkout_dir: Path, compdb: CompdbSpec) -> Path:
    """Resolve compdb path relative to checkout root if needed."""
    raw = Path(compdb.path)
    return raw if raw.is_absolute() else (repo_checkout_dir / raw)
