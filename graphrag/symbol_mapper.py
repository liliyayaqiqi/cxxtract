"""
SCIP Symbol -> Global URI Mapper (THE BRIDGE).

This module translates SCIP's native symbol format into our Global URI format,
enabling cross-database identity between the Vector DB (Qdrant) and Graph DB (Neo4j).

SCIP Symbol Format (from scip-clang):
    cxx . . $ YAML/GraphBuilderAdapter#OnSequenceStart(ff993a8f75aba5c3).
    ^scheme ^pkg  ^--- descriptors with suffixes ---^

Descriptor Suffixes:
    /  = Namespace (e.g., YAML/)
    #  = Type (class/struct)
    (hash). = Method
    .  = Term (free function or static member)
    !  = Macro

Our Global URI Format:
    [RepoName]::[FilePath]::[EntityType]::[EntityName]
    
Example mappings:
    SCIP: cxx . . $ YAML/GraphBuilderAdapter#
    -> Global URI: yaml-cpp::src/contrib/graphbuilderadapter.h::Class::YAML::GraphBuilderAdapter
    
    SCIP: cxx . . $ YAML/GraphBuilderAdapter#OnSequenceStart(hash).
    -> Global URI: yaml-cpp::..::Function::YAML::GraphBuilderAdapter::OnSequenceStart
"""

import logging
import re
from dataclasses import dataclass
from typing import Literal, Optional

from core.uri_contract import (
    create_global_uri,
    make_function_signature_hash,
    normalize_cpp_entity_name,
)
from graphrag.config import (
    IGNORED_NAMESPACES,
    MONITORED_NAMESPACES,
    MONITORED_NAMESPACE_OWNER_REPOS,
)
from graphrag.proto import scip_pb2

logger = logging.getLogger(__name__)
_SCIP_DISAMBIG_RE = re.compile(r"^[0-9a-zA-Z_-]{4,64}$")

# ---------------------------------------------------------------------------
# SCIP SymbolInformation.Kind enum values (source of truth: generated pb2)
# ---------------------------------------------------------------------------
SCIP_KIND_UNSPECIFIED = scip_pb2.SymbolInformation.Kind.UnspecifiedKind
SCIP_KIND_CLASS = scip_pb2.SymbolInformation.Kind.Class
SCIP_KIND_STRUCT = scip_pb2.SymbolInformation.Kind.Struct
SCIP_KIND_FUNCTION = scip_pb2.SymbolInformation.Kind.Function
SCIP_KIND_METHOD = scip_pb2.SymbolInformation.Kind.Method
SCIP_KIND_CONSTRUCTOR = scip_pb2.SymbolInformation.Kind.Constructor
SCIP_KIND_NAMESPACE = scip_pb2.SymbolInformation.Kind.Namespace
SCIP_KIND_VARIABLE = scip_pb2.SymbolInformation.Kind.Variable
SCIP_KIND_PARAMETER = scip_pb2.SymbolInformation.Kind.Parameter
SCIP_KIND_TYPE_PARAMETER = scip_pb2.SymbolInformation.Kind.TypeParameter
SCIP_KIND_FIELD = scip_pb2.SymbolInformation.Kind.Field
SCIP_KIND_ENUM = scip_pb2.SymbolInformation.Kind.Enum
SCIP_KIND_ENUM_MEMBER = scip_pb2.SymbolInformation.Kind.EnumMember
SCIP_KIND_MACRO = scip_pb2.SymbolInformation.Kind.Macro
SCIP_KIND_TYPE_ALIAS = scip_pb2.SymbolInformation.Kind.TypeAlias
SCIP_KIND_UNION = scip_pb2.SymbolInformation.Kind.Union

# ---- Dual-Brain Entity Type Contract ----
# The Graph DB (Neo4j) MUST use the exact same entity types as the
# Vector DB (Qdrant).  The Left Brain (tree-sitter extraction) produces
# exactly three types, defined in extraction/config.py:ENTITY_TYPE_MAP:
#
#   class_specifier       -> "Class"
#   struct_specifier      -> "Struct"
#   function_definition   -> "Function"
#
# Every SCIP symbol MUST be downcasted to one of these three, or dropped.
# No new entity types may be invented.
# ---------------------------------------------------------------------------

# SCIP Kind -> our entity type (secondary hint, only used when Kind != 0)
_KIND_TO_ENTITY_TYPE: dict[int, str] = {
    SCIP_KIND_CLASS:       "Class",
    SCIP_KIND_STRUCT:      "Struct",
    SCIP_KIND_UNION:       "Struct",     # Union -> Struct (closest Left Brain equivalent)
    SCIP_KIND_FUNCTION:    "Function",
    SCIP_KIND_METHOD:      "Function",   # Method -> Function
    SCIP_KIND_CONSTRUCTOR: "Function",   # Constructor -> Function
}

# SCIP Kind values that must be dropped (not Class, Struct, or Function)
_DROPPABLE_KINDS: set[int] = {
    SCIP_KIND_NAMESPACE,
    SCIP_KIND_VARIABLE,
    SCIP_KIND_PARAMETER,
    SCIP_KIND_TYPE_PARAMETER,
    SCIP_KIND_FIELD,
    SCIP_KIND_ENUM,
    SCIP_KIND_ENUM_MEMBER,
    SCIP_KIND_MACRO,
    SCIP_KIND_TYPE_ALIAS,
}

# Symbol disposition type
SymbolDisposition = Literal["keep", "drop", "stub"]


@dataclass
class ParsedScipSymbol:
    """Intermediate representation of a parsed SCIP symbol."""
    
    scheme: str
    namespace_parts: list[str]
    entity_type: str                 # "Class", "Struct", "Function"
    entity_name: str                 # Qualified name (e.g., YAML::GraphBuilderAdapter)
    package_manager: str
    package_name: str
    package_version: str
    function_sig_hash: Optional[str]
    is_external: bool
    is_local: bool
    is_macro: bool
    first_namespace: str             # Top-level namespace (e.g., "YAML", "std")


def parse_scip_symbol(scip_symbol: str, kind: int = 0) -> Optional[ParsedScipSymbol]:
    """Parse a SCIP symbol string into structured components.
    
    Args:
        scip_symbol: Raw SCIP symbol string (e.g., "cxx . . $ YAML/Foo#bar(hash).").
        kind: SymbolInformation.Kind enum value from SCIP protobuf.
        
    Returns:
        ParsedScipSymbol if parseable, None if should be skipped (local, macro, file-scope).
        
    Example:
        >>> parsed = parse_scip_symbol("cxx . . $ YAML/GraphBuilderAdapter#", kind=7)
        >>> parsed.entity_type
        'Class'
        >>> parsed.entity_name
        'YAML::GraphBuilderAdapter'
    """
    # Handle local symbols
    if scip_symbol.startswith("local "):
        return None
    
    # Split into scheme, package, descriptors
    # Format: "scheme manager name version descriptors"
    # For scip-clang: "cxx . . $ descriptors"
    # We need to find where the package ends and descriptors begin
    
    # The package portion is 3 space-separated parts, then comes descriptors
    parts = scip_symbol.split(" ", 4)
    if len(parts) < 5:
        logger.warning(f"Malformed SCIP symbol (too few parts): {scip_symbol}")
        return None
    
    scheme = parts[0]           # "cxx"
    manager = parts[1]          # package manager
    pkg_name = parts[2]         # package name
    version = parts[3]          # package version
    descriptor_str = parts[4]   # "YAML/GraphBuilderAdapter#..."
    
    # Skip non-cxx schemes
    if scheme != "cxx":
        return None
    
    # Parse descriptors (the critical part)
    # Descriptors follow the grammar: (<descriptor>)+
    # Each descriptor is a name followed by a suffix (/, #, ., !, etc.)
    
    namespace_parts: list[str] = []
    entity_type: Optional[str] = None
    final_name: Optional[str] = None
    is_macro = False
    function_sig_hash: Optional[str] = None
    
    # Walk through descriptor_str character by character
    i = 0
    while i < len(descriptor_str):
        # Extract descriptor name (up to suffix char)
        name_start = i
        
        # Handle backtick-escaped names
        if descriptor_str[i] == "`":
            # Find closing backtick
            i += 1
            name_start = i
            while i < len(descriptor_str) and descriptor_str[i] != "`":
                # Handle double-backtick escapes
                if descriptor_str[i] == "`" and i + 1 < len(descriptor_str) and descriptor_str[i + 1] == "`":
                    i += 2
                else:
                    i += 1
            
            name = descriptor_str[name_start:i]
            i += 1  # Skip closing backtick
        else:
            # Regular name (alphanumeric, _, +, -, $)
            while i < len(descriptor_str) and descriptor_str[i] not in "/#.!()[]":
                i += 1
            name = descriptor_str[name_start:i]
        
        # Get the suffix
        if i >= len(descriptor_str):
            break
        
        suffix = descriptor_str[i]
        
        # Handle special cases
        if suffix == "(":
            # Method with disambiguator: name(hash).
            # Find closing paren
            paren_start = i
            i += 1
            while i < len(descriptor_str) and descriptor_str[i] != ")":
                i += 1
            disambiguator = descriptor_str[paren_start + 1 : i]
            i += 1  # Skip )
            
            # Expect . after )
            if i < len(descriptor_str) and descriptor_str[i] == ".":
                i += 1
                # This is a method/function
                final_name = name
                entity_type = "Function"
                if disambiguator:
                    lowered = disambiguator.lower()
                    if _SCIP_DISAMBIG_RE.match(lowered):
                        function_sig_hash = f"sig_{lowered}"
                    else:
                        function_sig_hash = make_function_signature_hash(disambiguator)
                break
        elif suffix == "/":
            # Namespace
            namespace_parts.append(name)
            i += 1
        elif suffix == "#":
            # Type (Class or Struct)
            # This could be:
            # 1. A class/struct definition: YAML/Foo# (no more descriptors)
            # 2. A method parent: YAML/Foo#method(). (more descriptors follow)
            
            # Store the type name in namespace_parts
            namespace_parts.append(name)
            
            # Mark as a type entity (may be overridden if method follows).
            # Use Kind as secondary hint if available, else default to Class.
            # scip-clang typically sets Kind=0, so the fallback fires.
            final_name = name
            entity_type = _KIND_TO_ENTITY_TYPE.get(kind, "Class")
            
            i += 1
        elif suffix == ".":
            # Term (free function or static member)
            final_name = name
            entity_type = "Function"
            i += 1
        elif suffix == "!":
            # Macro — skip these
            is_macro = True
            break
        else:
            i += 1
    
    # Skip macros and file-scope symbols
    if is_macro or final_name is None:
        return None
    
    if final_name.startswith("<file>/"):
        return None
    
    # Drop symbols whose Kind explicitly indicates a non-entity type
    # (variables, parameters, fields, enums, etc.).
    # When Kind=0 (UnspecifiedKind, the scip-clang default), we rely
    # on the descriptor suffix which has already set entity_type above.
    if kind in _DROPPABLE_KINDS:
        return None
    
    # Build qualified entity_name
    # For classes (suffix #), namespace_parts already includes the class name,
    # so we need to deduplicate
    if entity_type in ("Class", "Struct") and namespace_parts and namespace_parts[-1] == final_name:
        # Remove duplicate (class name was added to both namespace_parts and final_name)
        entity_name = "::".join(namespace_parts)
    else:
        entity_name = "::".join(namespace_parts + [final_name])
    
    # Determine first (top-level) namespace for filtering decisions
    first_ns = namespace_parts[0] if namespace_parts else ""
    
    # Package-local symbols for scip-clang typically use ". . $".
    # A symbol is treated as external if either:
    # 1) it belongs to a non-local package, or
    # 2) its top namespace is outside MONITORED_NAMESPACES.
    # This preserves historical "std:: is external" behavior while allowing
    # monitored cross-repo symbols (non-local package) to become stubs.
    is_package_local = manager == "." and pkg_name == "."
    namespace_external = first_ns not in MONITORED_NAMESPACES and first_ns != ""
    is_external = (not is_package_local) or namespace_external
    
    # Default entity_type if not yet determined
    if entity_type is None:
        entity_type = "Function"  # Default fallback
    
    # Dual-Brain contract enforcement: entity_type MUST be one of the
    # three types the Left Brain (tree-sitter/Qdrant) produces.
    # Anything else would break Global URI joins across databases.
    _ALLOWED_ENTITY_TYPES = {"Class", "Struct", "Function"}
    if entity_type not in _ALLOWED_ENTITY_TYPES:
        logger.warning(
            f"Unexpected entity_type '{entity_type}' for {scip_symbol} — dropping"
        )
        return None
    
    return ParsedScipSymbol(
        scheme=scheme,
        namespace_parts=namespace_parts,
        entity_type=entity_type,
        entity_name=normalize_cpp_entity_name(entity_name),
        package_manager=manager,
        package_name=pkg_name,
        package_version=version,
        function_sig_hash=function_sig_hash,
        is_external=is_external,
        is_local=False,
        is_macro=False,
        first_namespace=first_ns,
    )


def classify_symbol(
    scip_symbol: str,
    kind: int = 0,
    is_local_definition: Optional[bool] = None,
) -> SymbolDisposition:
    """Classify a SCIP symbol as keep, drop, or stub.
    
    Decision tree:
    
    1. Local / macro / file-scope / unparseable -> ``"drop"``
    2. ``first_namespace`` in ``IGNORED_NAMESPACES`` -> ``"drop"``
    3. ``first_namespace`` in ``MONITORED_NAMESPACES`` and symbol is NOT
       local to current index -> ``"stub"``
    4. ``first_namespace`` in ``MONITORED_NAMESPACES`` -> ``"keep"``
    5. Everything else (unknown namespace, no namespace) -> ``"keep"``
       (conservative — let the graph grow; can always prune later)
    
    **Cross-repo contract**: A ``"stub"`` disposition means the symbol
    belongs to a sibling repository.  The caller should create a stub
    node in Neo4j (``is_external=True``, ``file_path="<external>"``)
    so that when the sibling repo is indexed later, ``MERGE`` will
    complete the node.
    
    Args:
        scip_symbol: Raw SCIP symbol string.
        kind: SymbolInformation.Kind enum value.
        is_local_definition: Whether symbol is known to be defined in the
            current SCIP index. ``False`` enables cross-repo stub behavior.
        
    Returns:
        ``"keep"``  — ingest as a full node.
        ``"drop"``  — silently discard.
        ``"stub"``  — ingest as an external stub node.
        
    Example:
        >>> classify_symbol("cxx . . $ std/string#")
        'drop'
        >>> classify_symbol("cxx . . $ YAML/Node#")
        'keep'
        >>> classify_symbol("cxx . . $ webrtc/RtpSender#")  # defined in another repo
        'stub'  # if webrtc is MONITORED and the caller knows it's not local
    """
    parsed = parse_scip_symbol(scip_symbol, kind)
    
    if parsed is None:
        return "drop"
    
    first_ns = parsed.first_namespace
    
    # Rule 1: IGNORED_NAMESPACES -> always drop
    if first_ns in IGNORED_NAMESPACES:
        return "drop"
    
    # Determine local vs external context. Prefer caller-provided index context.
    local_def = is_local_definition
    if local_def is None:
        local_def = not parsed.is_external

    # Rule 2: MONITORED_NAMESPACES and non-local -> stub for cross-repo
    if first_ns in MONITORED_NAMESPACES and not local_def:
        return "stub"
    
    # Rule 3: MONITORED_NAMESPACES and local -> keep
    if first_ns in MONITORED_NAMESPACES:
        return "keep"
    
    # Rule 4: Unknown namespace -> keep (conservative)
    return "keep"


def scip_symbol_to_global_uri(
    scip_symbol: str,
    file_path: str,
    repo_name: str,
    kind: int = 0,
    include_function_sig: bool = False,
) -> Optional[str]:
    """Convert a SCIP symbol to a Global URI.
    
    This is the **single source of truth** for cross-database identity.
    The resulting URI can be used to:
    - Look up the entity in Qdrant (vector search)
    - Create/merge nodes in Neo4j (graph topology)
    
    Args:
        scip_symbol: Raw SCIP symbol string.
        file_path: Document.relative_path from SCIP.
        repo_name: Repository name.
        kind: SymbolInformation.Kind enum value.
        include_function_sig: If True, include function signature discriminator
            from SCIP method disambiguator in Function URIs.
        
    Returns:
        Global URI string, or None if symbol should be skipped.
        
    Example:
        >>> uri = scip_symbol_to_global_uri(
        ...     "cxx . . $ YAML/GraphBuilderAdapter#",
        ...     "src/contrib/graphbuilderadapter.h",
        ...     "yaml-cpp",
        ...     kind=7
        ... )
        >>> uri
        'yaml-cpp::src/contrib/graphbuilderadapter.h::Class::YAML::GraphBuilderAdapter'
    """
    parsed = parse_scip_symbol(scip_symbol, kind)
    
    if parsed is None:
        return None
    
    # For external symbols with no file_path, use sentinel value
    if parsed.is_external and file_path == "":
        file_path = "<external>"
    
    return create_global_uri(
        repo_name=repo_name,
        file_path=file_path,
        entity_type=parsed.entity_type,
        entity_name=parsed.entity_name,
        function_sig_hash=(
            parsed.function_sig_hash
            if include_function_sig and parsed.entity_type == "Function"
            else None
        ),
    )


def resolve_symbol_owner_repo(
    scip_symbol: str,
    current_repo_name: str,
    kind: int = 0,
) -> str:
    """Resolve owner repo for a symbol, defaulting to current repo.

    For monitored cross-repo stubs this function maps top-level namespace to
    the configured owner repository, enabling stable cross-repo URI identity.
    """
    parsed = parse_scip_symbol(scip_symbol, kind)
    if parsed is None:
        return current_repo_name
    return MONITORED_NAMESPACE_OWNER_REPOS.get(parsed.first_namespace, current_repo_name)


def scip_symbol_to_entity_name(scip_symbol: str) -> Optional[str]:
    """Extract just the qualified entity name from a SCIP symbol.
    
    Useful for debugging and logging.
    
    Args:
        scip_symbol: Raw SCIP symbol string.
        
    Returns:
        Qualified name (e.g., "YAML::GraphBuilderAdapter::OnSequenceStart"),
        or None if unparseable.
    """
    parsed = parse_scip_symbol(scip_symbol)
    return parsed.entity_name if parsed else None


def is_external_symbol(scip_symbol: str) -> bool:
    """Check if a SCIP symbol refers to an external dependency.
    
    A symbol is external when it belongs to a non-local package or when its
    top-level namespace is outside ``MONITORED_NAMESPACES``. Note that
    ``IGNORED_NAMESPACES`` symbols are external as well, but are **dropped**
    entirely by ``classify_symbol()`` before reaching the graph.
    
    Args:
        scip_symbol: Raw SCIP symbol string.
        
    Returns:
        True if symbol is from an external dependency.
    """
    parsed = parse_scip_symbol(scip_symbol)
    return parsed.is_external if parsed else False


def should_drop_symbol(
    scip_symbol: str,
    kind: int = 0,
    is_local_definition: Optional[bool] = None,
) -> bool:
    """Quick predicate: should this symbol be silently discarded?
    
    Convenience wrapper around ``classify_symbol()`` for use in
    tight filter loops.
    
    Args:
        scip_symbol: Raw SCIP symbol string.
        kind: SymbolInformation.Kind enum value.
        is_local_definition: Optional index-locality context.
        
    Returns:
        True if the symbol should be discarded from all pipelines.
    """
    return classify_symbol(
        scip_symbol,
        kind,
        is_local_definition=is_local_definition,
    ) == "drop"
