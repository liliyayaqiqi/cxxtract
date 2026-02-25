"""Workspace-level symbol ownership catalog for cross-repo graph ingestion."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from graphrag.scip_parser import ScipParseResult
from graphrag.symbol_mapper import parse_scip_symbol


@dataclass(frozen=True)
class SymbolConflict:
    """Represents a symbol owner conflict across multiple repositories."""

    scip_symbol: str
    owner_repo: str
    candidate_repos: list[str]
    reason: str


@dataclass
class WorkspaceSymbolCatalog:
    """Catalog for deterministic symbol ownership and owner file resolution."""

    symbol_owner_repo: dict[str, str] = field(default_factory=dict)
    symbol_owner_file: dict[tuple[str, str], str] = field(default_factory=dict)
    conflicts: list[SymbolConflict] = field(default_factory=list)

    def resolve_owner_repo(self, scip_symbol: str) -> Optional[str]:
        """Resolve owner repo for a symbol, if known."""
        return self.symbol_owner_repo.get(scip_symbol)

    def resolve_owner_file(self, owner_repo: str, scip_symbol: str) -> Optional[str]:
        """Resolve owner file path for a symbol in owner repo, if known."""
        return self.symbol_owner_file.get((owner_repo, scip_symbol))


def build_workspace_symbol_catalog(
    repo_parse_results: list[tuple[str, ScipParseResult]],
    owner_overrides: Optional[dict[str, str]] = None,
) -> WorkspaceSymbolCatalog:
    """Build workspace symbol ownership catalog from parsed SCIP payloads.

    Ownership is inferred from locally defined symbols in each repo parse result.
    On conflict, deterministic precedence is applied:
    1) explicit ``owner_overrides`` entry for symbol
    2) package-name hint from SCIP symbol metadata
    3) stable repository iteration order
    """
    overrides = owner_overrides or {}
    candidates: dict[str, list[tuple[str, str]]] = {}

    for repo_name, parse_result in repo_parse_results:
        for sym in parse_result.symbols:
            if not sym.is_local_definition:
                continue
            candidates.setdefault(sym.scip_symbol, []).append((repo_name, sym.file_path))

    catalog = WorkspaceSymbolCatalog()

    for scip_symbol, repos_and_files in candidates.items():
        repos = [repo for repo, _ in repos_and_files]
        owner_repo: Optional[str] = None
        reason = "single_local_definition"

        if scip_symbol in overrides:
            override_owner = overrides[scip_symbol]
            if override_owner in repos:
                owner_repo = override_owner
                reason = "override"

        if owner_repo is None and len(repos) > 1:
            parsed = parse_scip_symbol(scip_symbol)
            if parsed is not None and parsed.package_name in repos and parsed.package_name != ".":
                owner_repo = parsed.package_name
                reason = "package_hint"

        if owner_repo is None:
            owner_repo = repos[0]
            if len(repos) > 1:
                reason = "stable_order"

        catalog.symbol_owner_repo[scip_symbol] = owner_repo

        owner_file = next(
            (path for repo, path in repos_and_files if repo == owner_repo),
            repos_and_files[0][1],
        )
        catalog.symbol_owner_file[(owner_repo, scip_symbol)] = owner_file

        if len(repos) > 1:
            catalog.conflicts.append(
                SymbolConflict(
                    scip_symbol=scip_symbol,
                    owner_repo=owner_repo,
                    candidate_repos=sorted(set(repos)),
                    reason=reason,
                )
            )

    return catalog
