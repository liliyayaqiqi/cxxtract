"""
SCIP Index Parser — Deserialize .scip protobuf into Python dataclasses.

Reads a SCIP index file and extracts:
- Symbol definitions with their relationships (inheritance, etc.)
- Reference occurrences with enclosing scope (for CALLS edge derivation)

Filtering is applied at parse time via ``classify_symbol()`` so that
ignored namespaces (std::, boost::, etc.) never enter the pipeline.
"""

import logging
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


def _build_enclosing_scope_map(
    doc: scip_pb2.Document,
) -> dict[int, str]:
    """Build a line -> enclosing definition symbol map.
    
    For each definition occurrence, record which lines it spans so that
    later we can attribute references to their containing scope.
    
    Args:
        doc: SCIP Document message.
        
    Returns:
        Dict mapping line numbers to the SCIP symbol that defines that scope.
    """
    scope_map: dict[int, str] = {}
    
    for occ in doc.occurrences:
        # Check if this is a definition
        is_def = (occ.symbol_roles & 0x1) > 0
        
        if not is_def or not occ.symbol:
            continue
        
        # Determine the range this definition spans
        # Prefer enclosing_range if available, else use range
        range_data = occ.enclosing_range if occ.enclosing_range else occ.range
        
        if len(range_data) >= 3:
            start_line = range_data[0]
            # If 4 elements: [start_line, start_char, end_line, end_char]
            # If 3 elements: [start_line, start_char, end_char] (same line)
            if len(range_data) == 4:
                end_line = range_data[2]
            else:
                end_line = start_line
            
            # Map all lines in this range to this symbol
            for line in range(start_line, end_line + 1):
                # Prefer narrower scopes (inner definitions override outer)
                # So don't overwrite if already set
                if line not in scope_map:
                    scope_map[line] = occ.symbol
    
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
    
    # Process each document
    for doc in index.documents:
        file_path = doc.relative_path
        
        # Build enclosing scope map for this document
        scope_map = _build_enclosing_scope_map(doc)
        
        # Extract symbol definitions
        for sym_info in doc.symbols:
            # Skip local symbols (not globally addressable)
            if sym_info.symbol.startswith("local "):
                continue
            
            # Smart filtering: classify before spending effort on extraction
            disposition = classify_symbol(sym_info.symbol, sym_info.kind)
            if disposition == "drop":
                dropped_syms += 1
                continue
            
            # Extract relationships, filtering targets too
            rels: list[ScipRelationship] = []
            for rel in sym_info.relationships:
                tgt_disp = classify_symbol(rel.symbol)
                if tgt_disp == "drop":
                    continue
                rels.append(
                    ScipRelationship(
                        target_symbol=rel.symbol,
                        is_reference=rel.is_reference,
                        is_implementation=rel.is_implementation,
                        is_type_definition=rel.is_type_definition,
                        is_definition=rel.is_definition,
                    )
                )
            
            # Find definition range from occurrences
            def_range: Optional[tuple[int, int, int, int]] = None
            for occ in doc.occurrences:
                if occ.symbol == sym_info.symbol and (occ.symbol_roles & 0x1):
                    # This is a definition occurrence
                    if len(occ.range) >= 3:
                        if len(occ.range) == 4:
                            def_range = (
                                occ.range[0],
                                occ.range[1],
                                occ.range[2],
                                occ.range[3],
                            )
                        else:
                            def_range = (
                                occ.range[0],
                                occ.range[1],
                                occ.range[0],
                                occ.range[2],
                            )
                    break
            
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
            if should_drop_symbol(occ.symbol):
                dropped_refs += 1
                continue
            
            # Also drop references FROM ignored enclosing symbols
            enclosing_sym = scope_map.get(occ.range[0] if len(occ.range) >= 3 else -1)
            if enclosing_sym and should_drop_symbol(enclosing_sym):
                dropped_refs += 1
                continue
            
            # Determine which definition this reference falls within
            if len(occ.range) >= 3:
                line = occ.range[0]
            else:
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
