"""
SCIP Index Parser — Deserialize .scip protobuf into Python dataclasses.

Reads a SCIP index file and extracts:
- Symbol definitions with their relationships (inheritance, etc.)
- Reference occurrences with enclosing scope (for CALLS edge derivation)

Filtering is applied at parse time via ``classify_symbol()`` so that
ignored namespaces (std::, boost::, etc.) never enter the pipeline.
"""

import logging
import heapq
from dataclasses import dataclass, field
from typing import Optional

from graphrag.proto import scip_pb2
from graphrag.symbol_mapper import classify_symbol, should_drop_symbol

logger = logging.getLogger(__name__)


@dataclass
class ScipRelationship:
    """A relationship between two SCIP symbols."""
    
    target_symbol: str          # SCIP symbol of the related entity
    is_reference: bool
    is_implementation: bool
    is_type_definition: bool
    is_definition: bool


@dataclass
class ScipSymbolDef:
    """A symbol definition extracted from SCIP."""
    
    scip_symbol: str            # Raw SCIP symbol string
    file_path: str              # Document.relative_path
    kind: int                   # SymbolInformation.Kind enum value
    display_name: str           # SymbolInformation.display_name
    definition_range: Optional[tuple[int, int, int, int]] = None
    relationships: list[ScipRelationship] = field(default_factory=list)


@dataclass
class ScipReference:
    """A reference occurrence (non-definition) found in a document."""
    
    scip_symbol: str            # Symbol being referenced
    file_path: str              # Document where the reference occurs
    enclosing_symbol: Optional[str]  # Nearest enclosing definition scope
    role: str                   # "READ", "WRITE", "CALL", "REF"
    line: int                   # 0-indexed line number


@dataclass
class ScipParseResult:
    """Result of parsing a SCIP index."""
    
    symbols: list[ScipSymbolDef]
    references: list[ScipReference]
    document_count: int
    external_symbol_count: int
    dropped_symbol_count: int = 0
    dropped_reference_count: int = 0


def _infer_role_from_symbol_roles(symbol_roles: int) -> str:
    """Infer a human-readable role from the SCIP symbol_roles bitfield.
    
    Args:
        symbol_roles: Bitfield from Occurrence.symbol_roles.
        
    Returns:
        "READ", "WRITE", "CALL", or "REF".
    """
    # SymbolRole enum values:
    # Definition = 0x1, Import = 0x2, WriteAccess = 0x4, ReadAccess = 0x8
    
    if symbol_roles & 0x4:  # WriteAccess
        return "WRITE"
    if symbol_roles & 0x8:  # ReadAccess
        return "READ"
    # If no specific role, default to generic reference
    # (most function calls fall here)
    return "CALL"


def _occurrence_line_bounds(occ: scip_pb2.Occurrence) -> Optional[tuple[int, int]]:
    """Extract line bounds from occurrence (preferring enclosing_range)."""
    range_data = occ.enclosing_range if occ.enclosing_range else occ.range
    if len(range_data) < 3:
        return None
    start_line = range_data[0]
    end_line = range_data[2] if len(range_data) == 4 else start_line
    return start_line, end_line


def _occurrence_quad_range(occ: scip_pb2.Occurrence) -> Optional[tuple[int, int, int, int]]:
    """Extract normalized 4-tuple source range from occurrence.range."""
    if len(occ.range) < 3:
        return None
    if len(occ.range) == 4:
        return (occ.range[0], occ.range[1], occ.range[2], occ.range[3])
    return (occ.range[0], occ.range[1], occ.range[0], occ.range[2])


def _collect_definition_ranges(doc: scip_pb2.Document) -> dict[str, tuple[int, int, int, int]]:
    """Collect first definition occurrence range for each symbol in a document."""
    ranges: dict[str, tuple[int, int, int, int]] = {}
    for occ in doc.occurrences:
        if not occ.symbol or not (occ.symbol_roles & 0x1):
            continue
        if occ.symbol in ranges:
            continue
        normalized = _occurrence_quad_range(occ)
        if normalized is not None:
            ranges[occ.symbol] = normalized
    return ranges


def _collect_index_definition_symbols(index: scip_pb2.Index) -> set[str]:
    """Collect symbols that are locally defined via Definition occurrences."""
    symbols: set[str] = set()
    for doc in index.documents:
        for occ in doc.occurrences:
            if not occ.symbol or occ.symbol.startswith("local "):
                continue
            if occ.symbol_roles & 0x1:
                symbols.add(occ.symbol)
    return symbols


def _build_enclosing_scope_map(
    doc: scip_pb2.Document,
    reference_lines: Optional[set[int]] = None,
) -> dict[int, str]:
    """Build a line -> enclosing definition symbol map.
    
    For each definition occurrence, record which lines it spans so that
    later we can attribute references to their containing scope.
    
    Args:
        doc: SCIP Document message.
        reference_lines: Optional set of line numbers that require lookup.
            When provided, resolves only those lines via sweep-line logic.
        
    Returns:
        Dict mapping line numbers to the SCIP symbol that defines that scope.
    """
    spans: list[tuple[int, int, int, str]] = []  # (start, end, width, symbol)
    for occ in doc.occurrences:
        # Check if this is a definition
        is_def = (occ.symbol_roles & 0x1) > 0
        
        if not is_def or not occ.symbol:
            continue
        
        bounds = _occurrence_line_bounds(occ)
        if bounds is None:
            continue

        start_line, end_line = bounds
        span_width = max(0, end_line - start_line)
        spans.append((start_line, end_line, span_width, occ.symbol))

    if reference_lines is None:
        # Fallback: derive query lines from non-definition occurrences.
        reference_lines = {
            occ.range[0]
            for occ in doc.occurrences
            if len(occ.range) >= 3 and not (occ.symbol_roles & 0x1)
        }

    if not spans or not reference_lines:
        return {}

    spans.sort(key=lambda item: item[0])
    sorted_lines = sorted(reference_lines)

    # Heap key: (span_width ASC, -start_line ASC => deeper scope first, symbol ASC).
    active: list[tuple[int, int, str, int]] = []  # (width, -start, symbol, end)
    scope_map: dict[int, str] = {}

    span_idx = 0
    for line in sorted_lines:
        while span_idx < len(spans) and spans[span_idx][0] <= line:
            start, end, width, symbol = spans[span_idx]
            heapq.heappush(active, (width, -start, symbol, end))
            span_idx += 1

        # Drop expired intervals.
        while active and active[0][3] < line:
            heapq.heappop(active)

        if active:
            scope_map[line] = active[0][2]

    return scope_map


def parse_scip_index(index_path: str, repo_name: str) -> ScipParseResult:
    """Parse a SCIP index file into structured Python objects.
    
    Args:
        index_path: Path to the .scip index file.
        repo_name: Repository name (not stored in SCIP, needed for URI generation).
        
    Returns:
        ScipParseResult containing all symbols and references.
        
    Raises:
        FileNotFoundError: If index file doesn't exist.
        
    Example:
        >>> result = parse_scip_index("output/index.scip", "yaml-cpp")
        >>> print(f"Parsed {result.document_count} documents")
    """
    logger.info(f"Parsing SCIP index: {index_path}")
    
    with open(index_path, "rb") as f:
        index = scip_pb2.Index()
        index.ParseFromString(f.read())
    
    logger.info(
        f"SCIP index loaded: tool={index.metadata.tool_info.name} "
        f"v{index.metadata.tool_info.version}, "
        f"project_root={index.metadata.project_root}"
    )
    
    symbols: list[ScipSymbolDef] = []
    references: list[ScipReference] = []
    dropped_syms = 0
    dropped_refs = 0

    local_definition_symbols = _collect_index_definition_symbols(index)
    
    # Process each document
    for doc in index.documents:
        file_path = doc.relative_path
        
        reference_lines = {
            occ.range[0]
            for occ in doc.occurrences
            if len(occ.range) >= 3 and not (occ.symbol_roles & 0x1)
        }
        # Build enclosing scope map for this document.
        scope_map = _build_enclosing_scope_map(doc, reference_lines=reference_lines)
        definition_ranges = _collect_definition_ranges(doc)
        
        # Extract symbol definitions
        for sym_info in doc.symbols:
            # Skip local symbols (not globally addressable)
            if sym_info.symbol.startswith("local "):
                continue
            
            # Smart filtering: classify before spending effort on extraction
            disposition = classify_symbol(
                sym_info.symbol,
                sym_info.kind,
                is_local_definition=(sym_info.symbol in local_definition_symbols),
            )
            if disposition == "drop":
                dropped_syms += 1
                continue
            if disposition == "stub":
                logger.debug("Treating symbol as stub (non-local definition): %s", sym_info.symbol)
            
            # Extract relationships, filtering targets too
            rels: list[ScipRelationship] = []
            for rel in sym_info.relationships:
                tgt_disp = classify_symbol(
                    rel.symbol,
                    is_local_definition=(rel.symbol in local_definition_symbols),
                )
                if tgt_disp == "drop":
                    continue
                if tgt_disp == "stub":
                    logger.debug(
                        "Treating relationship target as stub: src=%s target=%s",
                        sym_info.symbol,
                        rel.symbol,
                    )
                rels.append(
                    ScipRelationship(
                        target_symbol=rel.symbol,
                        is_reference=rel.is_reference,
                        is_implementation=rel.is_implementation,
                        is_type_definition=rel.is_type_definition,
                        is_definition=rel.is_definition,
                    )
                )
            
            def_range = definition_ranges.get(sym_info.symbol)
            
            symbols.append(
                ScipSymbolDef(
                    scip_symbol=sym_info.symbol,
                    file_path=file_path,
                    kind=sym_info.kind,
                    display_name=sym_info.display_name,
                    definition_range=def_range,
                    relationships=rels,
                )
            )
        
        # Extract reference occurrences (for CALLS edges)
        for occ in doc.occurrences:
            is_def = (occ.symbol_roles & 0x1) > 0
            
            if is_def or not occ.symbol or occ.symbol.startswith("local "):
                continue
            
            # Smart filtering: drop references TO ignored namespaces
            occ_local = occ.symbol in local_definition_symbols
            occ_disp = classify_symbol(
                occ.symbol,
                is_local_definition=occ_local,
            )
            if occ_disp == "drop":
                dropped_refs += 1
                continue
            if occ_disp == "stub":
                logger.debug("Reference target resolved as stub: %s", occ.symbol)
            
            # Also drop references FROM ignored enclosing symbols
            line = occ.range[0] if len(occ.range) >= 3 else -1
            enclosing_sym = scope_map.get(line)
            if enclosing_sym and should_drop_symbol(
                enclosing_sym,
                is_local_definition=(enclosing_sym in local_definition_symbols),
            ):
                dropped_refs += 1
                continue
            
            # Determine which definition this reference falls within
            if len(occ.range) < 3:
                continue
            
            enclosing = scope_map.get(line)
            role = _infer_role_from_symbol_roles(occ.symbol_roles)
            
            references.append(
                ScipReference(
                    scip_symbol=occ.symbol,
                    file_path=file_path,
                    enclosing_symbol=enclosing,
                    role=role,
                    line=line,
                )
            )
    
    logger.info(
        f"Parsed {len(symbols)} symbol definitions, "
        f"{len(references)} references from {len(index.documents)} documents "
        f"(dropped {dropped_syms} symbols, {dropped_refs} references)"
    )
    
    return ScipParseResult(
        symbols=symbols,
        references=references,
        document_count=len(index.documents),
        external_symbol_count=len(index.external_symbols),
        dropped_symbol_count=dropped_syms,
        dropped_reference_count=dropped_refs,
    )
