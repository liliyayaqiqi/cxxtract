"""
AST traversal and entity extraction logic.

This module provides functions to traverse the C++ AST and extract entities
(classes, functions) along with their associated Doxygen comments.
"""

import logging
import re
from typing import List, Optional, Tuple
from tree_sitter import Node, Tree

from core.uri_contract import make_function_signature_hash, normalize_cpp_entity_name
from extraction.config import (
    TARGET_ENTITY_TYPES,
    TEMPLATE_WRAPPER,
    NAMESPACE_NODE,
    COMMENT_NODE,
    CONTAINER_TYPES,
    TRANSPARENT_WRAPPERS,
    DECLARATION_NODE,
    DOXYGEN_PREFIXES,
    ENTITY_TYPE_MAP,
    PREPROCESSOR_CONTAINERS,
    DEFAULT_INCLUDE_DECLARATIONS,
    DEFAULT_EXTERN_C_DECLARATIONS,
)
from extraction.models import ExtractedEntity

logger = logging.getLogger(__name__)
_SPACE_RE = re.compile(r"\s+")


def is_doxygen_comment(comment_text: str) -> bool:
    """Check if a comment is a Doxygen-style documentation comment.
    
    Args:
        comment_text: The text content of the comment.
        
    Returns:
        True if the comment starts with Doxygen markers (///, /**, //!, /*!)
    """
    stripped = comment_text.strip()
    # MUST strictly match Doxygen prefixes only
    return any(stripped.startswith(prefix) for prefix in DOXYGEN_PREFIXES)


def clean_doxygen_comment(comment_text: str) -> str:
    """Strip Doxygen comment delimiters and leading asterisks.
    
    Removes:
    - /// at the start of each line
    - /** and */ delimiters
    - Leading * on each line
    - /*! and */ delimiters
    - //! at the start of each line
    
    Args:
        comment_text: Raw comment text with delimiters.
        
    Returns:
        Cleaned comment text.
    """
    lines = comment_text.split('\n')
    cleaned_lines = []
    
    for idx, line in enumerate(lines):
        stripped = line.strip()

        # Strip starting markers on the first line.
        if idx == 0:
            if stripped.startswith('///'):
                stripped = stripped[3:]
            elif stripped.startswith('//!'):
                stripped = stripped[3:]
            elif stripped.startswith('/**'):
                stripped = stripped[3:]
            elif stripped.startswith('/*!'):
                stripped = stripped[3:]
        else:
            if stripped.startswith('///'):
                stripped = stripped[3:]
            elif stripped.startswith('//!'):
                stripped = stripped[3:]

        stripped = stripped.strip()

        # Strip trailing block marker regardless of line position.
        if stripped.endswith('*/'):
            stripped = stripped[:-2].rstrip()

        # Strip continuation '*' in multiline block comments.
        if stripped.startswith('*'):
            stripped = stripped[1:].lstrip()
        
        if stripped:  # Only add non-empty lines
            cleaned_lines.append(stripped)
    
    return '\n'.join(cleaned_lines)


def get_preceding_comments(node: Node, source_bytes: bytes) -> Optional[str]:
    """Collect all Doxygen comments immediately preceding a definition node.
    
    This function walks backward through siblings to find comments that
    directly precede the given node (with at most 1 blank line gap).
    
    Args:
        node: The AST node to find comments for.
        source_bytes: The raw source file bytes.
        
    Returns:
        Cleaned Doxygen comment text, or None if no comments found.
    """
    comments = []
    sibling = node.prev_named_sibling
    expected_end_row = node.start_point.row
    
    while sibling is not None and sibling.type == COMMENT_NODE:
        # Check adjacency: allow at most 1 line gap
        gap = expected_end_row - sibling.end_point.row
        if gap > 1:
            break  # Blank line gap - stop collecting
        
        # Get comment text
        comment_text = source_bytes[sibling.start_byte:sibling.end_byte].decode("utf-8")
        
        # Only collect Doxygen comments (strict filtering)
        if is_doxygen_comment(comment_text):
            comments.append(comment_text)
        
        expected_end_row = sibling.start_point.row
        sibling = sibling.prev_named_sibling
    
    # Reverse to get source order
    comments.reverse()
    
    if comments:
        # Clean and join the Doxygen comments
        cleaned = [clean_doxygen_comment(c) for c in comments]
        return '\n'.join(cleaned)
    return None


def get_effective_node_for_extraction(node: Node) -> Tuple[Node, Node, bool]:
    """Get the effective nodes for comment search and code extraction.
    
    When a function/class is templated, the comment precedes the template_declaration,
    not the inner function_definition. This function handles that.
    
    Args:
        node: The entity node (function_definition, class_specifier, etc.)
        
    Returns:
        A tuple of (node_for_comments, node_for_code, is_templated) where:
        - node_for_comments: The node to search for preceding comments
        - node_for_code: The outermost node to extract code from
        - is_templated: Whether the entity is wrapped in a template
    """
    is_templated = False
    outer_node = node
    
    # Walk up to find wrapping template_declaration
    while outer_node.parent and outer_node.parent.type == TEMPLATE_WRAPPER:
        outer_node = outer_node.parent
        is_templated = True
    
    return outer_node, outer_node, is_templated


def is_function_definition(node: Node) -> bool:
    """Check if a node is an actual function definition (not just a declaration).
    
    Function definitions have a body (compound_statement or try_statement).
    Function declarations (prototypes) do not.
    
    Args:
        node: A function_definition node.
        
    Returns:
        True if this is a definition with a body, False if it's just a declaration.
    """
    if node.type != "function_definition":
        return False
    
    # Check if it has a body field
    body = node.child_by_field_name("body")
    return body is not None


def is_class_definition(node: Node) -> bool:
    """Check if a class/struct node is an actual definition (not forward declaration).
    
    Args:
        node: A class_specifier or struct_specifier node.
        
    Returns:
        True if this is a definition with a body, False if it's a forward declaration.
    """
    if node.type not in ("class_specifier", "struct_specifier"):
        return False
    
    # Check if it has a body field
    body = node.child_by_field_name("body")
    return body is not None


def extract_function_name(node: Node, source_bytes: bytes) -> Optional[str]:
    """Extract the name of a function from a function_definition node.
    
    Args:
        node: A function_definition node.
        source_bytes: The raw source file bytes.
        
    Returns:
        The function name, or None if it cannot be determined.
    """
    declarator = node.child_by_field_name("declarator")
    if not declarator:
        logger.debug(f"Function at line {node.start_point.row + 1} has no declarator")
        return None
    
    # For function_declarator, the name is in the "declarator" field
    if declarator.type == "function_declarator":
        name_node = declarator.child_by_field_name("declarator")
        if name_node and name_node.text:
            return normalize_cpp_entity_name(name_node.text.decode("utf-8"))
    
    # Fallback: try to get text directly from declarator
    if declarator.text:
        # For simple cases, the declarator itself might be the identifier
        raw_name = declarator.text.decode("utf-8").split('(')[0].strip()
        return normalize_cpp_entity_name(raw_name)
    
    return None


def extract_class_name(node: Node, source_bytes: bytes) -> Optional[str]:
    """Extract the name of a class/struct from a class_specifier/struct_specifier node.
    
    Args:
        node: A class_specifier or struct_specifier node.
        source_bytes: The raw source file bytes.
        
    Returns:
        The class/struct name, or None if it cannot be determined.
    """
    name_node = node.child_by_field_name("name")
    if name_node and name_node.text:
        return normalize_cpp_entity_name(name_node.text.decode("utf-8"))
    
    logger.debug(f"Class at line {node.start_point.row + 1} has no name (anonymous)")
    return None


def extract_namespace_name(node: Node, source_bytes: bytes) -> Optional[str]:
    """Extract the name of a namespace from a namespace_definition node.
    
    Args:
        node: A namespace_definition node.
        source_bytes: The raw source file bytes.
        
    Returns:
        The namespace name, or None for anonymous namespaces.
    """
    name_node = node.child_by_field_name("name")
    if name_node and name_node.text:
        return normalize_cpp_entity_name(name_node.text.decode("utf-8"))
    return None


def extract_function_declaration_name(node: Node) -> Optional[str]:
    """Extract function name from a declaration node containing function_declarator.

    Args:
        node: A declaration node.

    Returns:
        Function name if declaration is a function prototype, else None.
    """
    if node.type != DECLARATION_NODE:
        return None

    declarator = node.child_by_field_name("declarator")
    if declarator is None:
        return None

    if declarator.type == "function_declarator":
        name_node = declarator.child_by_field_name("declarator")
        if name_node and name_node.text:
            return normalize_cpp_entity_name(name_node.text.decode("utf-8"))
        return None

    # Some forms are wrapped one level deeper.
    inner = declarator.child_by_field_name("declarator")
    if inner and inner.type == "function_declarator":
        inner_name = inner.child_by_field_name("declarator")
        if inner_name and inner_name.text:
            return normalize_cpp_entity_name(inner_name.text.decode("utf-8"))

    return None


def _should_extract_declaration(
    include_declarations: bool,
    extern_context: bool,
    extern_c_declarations: bool,
) -> bool:
    """Determine if declaration-only functions should be emitted."""
    return include_declarations or (extern_context and extern_c_declarations)


def extract_declaration_entity(
    node: Node,
    source_bytes: bytes,
    repo_name: str,
    file_path: str,
    namespace_stack: List[str],
) -> Optional[ExtractedEntity]:
    """Extract a function declaration as an entity.

    Args:
        node: Declaration node containing a function prototype.
        source_bytes: Raw source bytes.
        repo_name: Repository name.
        file_path: Relative file path.
        namespace_stack: Active namespace stack.

    Returns:
        ExtractedEntity for declaration, or None if not a function declaration.
    """
    function_name = extract_function_declaration_name(node)
    if not function_name:
        return None

    if namespace_stack:
        qualified_name = normalize_cpp_entity_name("::".join(namespace_stack + [function_name]))
    else:
        qualified_name = function_name

    comment_node, code_node, is_templated = get_effective_node_for_extraction(node)
    docstring = get_preceding_comments(comment_node, source_bytes)
    code_text = source_bytes[code_node.start_byte:code_node.end_byte].decode("utf-8")

    global_uri = ExtractedEntity.create_uri(
        repo_name=repo_name,
        file_path=file_path,
        entity_type="Function",
        entity_name=qualified_name,
    )

    return ExtractedEntity(
        global_uri=global_uri,
        repo_name=repo_name,
        file_path=file_path,
        entity_type="Function",
        entity_name=qualified_name,
        docstring=docstring,
        code_text=code_text,
        start_line=code_node.start_point.row + 1,
        end_line=code_node.end_point.row + 1,
        is_templated=is_templated,
    )


def detect_macro_broken_class(node: Node, source_bytes: bytes) -> Optional[tuple]:
    """Detect if a function_definition is actually a class/struct broken by macros.
    
    Tree-sitter can misparse `class MACRO ClassName` as a function_definition.
    This function detects that pattern.
    
    Args:
        node: A function_definition node.
        source_bytes: The raw source file bytes.
        
    Returns:
        Tuple of (entity_type, entity_name) if detected, None otherwise.
    """
    if node.type != "function_definition":
        return None
    
    # Get the raw text of the node
    node_text = source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")
    
    # Check if it starts with "class " or "struct "
    stripped = node_text.strip()
    if stripped.startswith("class ") or stripped.startswith("struct "):
        # Extract the declarator (which should be the class/struct name)
        declarator = node.child_by_field_name("declarator")
        if declarator and declarator.text:
            name = normalize_cpp_entity_name(declarator.text.decode("utf-8"))
            entity_type = "Class" if stripped.startswith("class ") else "Struct"
            logger.info(f"Detected macro-broken {entity_type} '{name}' at line {node.start_point.row + 1}")
            return (entity_type, name)
    
    return None


def extract_entity_from_node(
    node: Node,
    source_bytes: bytes,
    repo_name: str,
    file_path: str,
    namespace_stack: List[str]
) -> Optional[ExtractedEntity]:
    """Extract a single entity (class/struct/function) from an AST node.
    
    Args:
        node: The entity node to extract.
        source_bytes: The raw source file bytes.
        repo_name: Repository name for URI generation.
        file_path: File path relative to repo root.
        namespace_stack: Current namespace qualification stack.
        
    Returns:
        An ExtractedEntity object, or None if extraction fails.
    """
    if node.type not in TARGET_ENTITY_TYPES:
        return None
    
    # BUG FIX #1: Check if this is a macro-broken class/struct
    macro_broken = detect_macro_broken_class(node, source_bytes)
    if macro_broken:
        entity_type, entity_name = macro_broken
    # Filter out forward declarations and prototypes
    elif node.type == "function_definition":
        if not is_function_definition(node):
            logger.debug(f"Skipping function declaration at line {node.start_point.row + 1}")
            return None
        entity_name = extract_function_name(node, source_bytes)
        entity_type = ENTITY_TYPE_MAP["function_definition"]
    elif node.type in ("class_specifier", "struct_specifier"):
        if not is_class_definition(node):
            logger.debug(f"Skipping forward declaration at line {node.start_point.row + 1}")
            return None
        entity_name = extract_class_name(node, source_bytes)
        entity_type = ENTITY_TYPE_MAP[node.type]
    else:
        logger.warning(f"Unknown entity type: {node.type}")
        return None
    
    if not entity_name:
        logger.debug(f"Skipping anonymous {node.type} at line {node.start_point.row + 1}")
        return None
    
    # Qualify with namespace
    if namespace_stack:
        qualified_name = "::".join(namespace_stack + [entity_name])
    else:
        qualified_name = entity_name
    qualified_name = normalize_cpp_entity_name(qualified_name)
    
    # Get effective nodes for extraction
    comment_node, code_node, is_templated = get_effective_node_for_extraction(node)
    
    # Extract comments
    docstring = get_preceding_comments(comment_node, source_bytes)
    
    # Extract code text
    code_text = source_bytes[code_node.start_byte:code_node.end_byte].decode("utf-8")
    
    # Get line numbers (1-indexed)
    start_line = code_node.start_point.row + 1
    end_line = code_node.end_point.row + 1
    
    # Build global URI
    global_uri = ExtractedEntity.create_uri(
        repo_name=repo_name,
        file_path=file_path,
        entity_type=entity_type,
        entity_name=qualified_name
    )
    
    entity = ExtractedEntity(
        global_uri=global_uri,
        repo_name=repo_name,
        file_path=file_path,
        entity_type=entity_type,
        entity_name=qualified_name,
        docstring=docstring,
        code_text=code_text,
        start_line=start_line,
        end_line=end_line,
        is_templated=is_templated
    )
    
    logger.debug(f"Extracted {entity_type}: {qualified_name} at {file_path}:{start_line}")
    return entity


def _signature_source_from_code_text(code_text: str) -> str:
    """Derive a stable signature source string from function code text."""
    text = code_text.strip()
    brace_idx = text.find("{")
    semi_idx = text.find(";")
    cut_idx = -1
    candidates = [idx for idx in (brace_idx, semi_idx) if idx >= 0]
    if candidates:
        cut_idx = min(candidates)
    if cut_idx >= 0:
        text = text[:cut_idx]
    return _SPACE_RE.sub(" ", text).strip()


def _disambiguate_overloaded_function_uris(entities: List[ExtractedEntity]) -> None:
    """Ensure overloaded functions in the same scope/file get distinct URIs."""
    groups: dict[tuple[str, str, str, str], list[ExtractedEntity]] = {}
    for entity in entities:
        if entity.entity_type != "Function":
            continue
        key = (
            entity.repo_name,
            entity.file_path,
            entity.entity_type,
            entity.entity_name,
        )
        groups.setdefault(key, []).append(entity)

    for group in groups.values():
        if len(group) <= 1:
            continue

        logger.info(
            "Detected %d overload candidates for function '%s' in %s",
            len(group),
            group[0].entity_name,
            group[0].file_path,
        )

        provisional_uris: list[str] = []
        for entity in group:
            signature_source = _signature_source_from_code_text(entity.code_text)
            sig_hash = make_function_signature_hash(signature_source)
            provisional_uris.append(
                ExtractedEntity.create_uri(
                    repo_name=entity.repo_name,
                    file_path=entity.file_path,
                    entity_type=entity.entity_type,
                    entity_name=entity.entity_name,
                    function_sig_hash=sig_hash,
                )
            )

        # If two entries still collide (e.g., declaration+definition with same signature),
        # add deterministic ordinal salt to keep URIs collision-free.
        collisions: dict[str, int] = {}
        for idx, entity in enumerate(group):
            uri = provisional_uris[idx]
            seen = collisions.get(uri, 0)
            collisions[uri] = seen + 1
            if seen == 0:
                entity.global_uri = uri
                continue

            signature_source = _signature_source_from_code_text(entity.code_text)
            salted_hash = make_function_signature_hash(
                f"{signature_source}|duplicate:{seen}",
            )
            entity.global_uri = ExtractedEntity.create_uri(
                repo_name=entity.repo_name,
                file_path=entity.file_path,
                entity_type=entity.entity_type,
                entity_name=entity.entity_name,
                function_sig_hash=salted_hash,
            )


def traverse_and_extract(
    node: Node,
    source_bytes: bytes,
    repo_name: str,
    file_path: str,
    namespace_stack: Optional[List[str]] = None,
    include_declarations: bool = DEFAULT_INCLUDE_DECLARATIONS,
    extern_c_declarations: bool = DEFAULT_EXTERN_C_DECLARATIONS,
    extern_context: bool = False,
) -> List[ExtractedEntity]:
    """Recursively traverse AST and extract all entities.
    
    This function walks the AST tree, handling namespaces, templates,
    and various wrapper nodes to extract all top-level entities.
    
    Args:
        node: The current AST node to traverse.
        source_bytes: The raw source file bytes.
        repo_name: Repository name for URI generation.
        file_path: File path relative to repo root.
        namespace_stack: Current namespace qualification stack.
        include_declarations: Whether to extract declaration-only functions.
        extern_c_declarations: Whether to extract declaration-only functions
            from inside extern "C" wrappers.
        extern_context: Internal flag indicating traversal is currently inside
            an extern "C" context.
        
    Returns:
        List of all extracted entities found in the tree.
    """
    if namespace_stack is None:
        namespace_stack = []
    
    entities = []
    
    for child in node.children:
        # Skip non-named nodes (like punctuation)
        if not child.is_named:
            continue
        
        # Handle template wrappers
        if child.type == TEMPLATE_WRAPPER:
            # Find the inner entity
            for template_child in child.children:
                if template_child.type in TARGET_ENTITY_TYPES:
                    entity = extract_entity_from_node(
                        template_child, source_bytes, repo_name, file_path, namespace_stack
                    )
                    if entity:
                        entities.append(entity)
                    break
                # Template can also wrap a declaration containing a class
                elif template_child.type == DECLARATION_NODE:
                    type_node = template_child.child_by_field_name("type")
                    if type_node and type_node.type in TARGET_ENTITY_TYPES:
                        entity = extract_entity_from_node(
                            type_node, source_bytes, repo_name, file_path, namespace_stack
                        )
                        if entity:
                            entities.append(entity)
                        break
                    if _should_extract_declaration(
                        include_declarations=include_declarations,
                        extern_context=extern_context,
                        extern_c_declarations=extern_c_declarations,
                    ):
                        decl_entity = extract_declaration_entity(
                            template_child,
                            source_bytes,
                            repo_name,
                            file_path,
                            namespace_stack,
                        )
                        if decl_entity:
                            entities.append(decl_entity)
                            break
        
        # Handle namespace definitions - recurse with updated stack
        elif child.type == NAMESPACE_NODE:
            ns_name = extract_namespace_name(child, source_bytes)
            new_stack = namespace_stack.copy()
            if ns_name:  # Named namespace
                new_stack.append(ns_name)
            # Recurse into namespace body
            body = child.child_by_field_name("body")
            if body:
                entities.extend(
                    traverse_and_extract(
                        body,
                        source_bytes,
                        repo_name,
                        file_path,
                        new_stack,
                        include_declarations=include_declarations,
                        extern_c_declarations=extern_c_declarations,
                        extern_context=extern_context,
                    )
                )
        
        # Handle declaration nodes (classes can be wrapped in these)
        elif child.type == DECLARATION_NODE:
            type_node = child.child_by_field_name("type")
            if type_node and type_node.type in TARGET_ENTITY_TYPES:
                entity = extract_entity_from_node(
                    type_node, source_bytes, repo_name, file_path, namespace_stack
                )
                if entity:
                    entities.append(entity)
            elif _should_extract_declaration(
                include_declarations=include_declarations,
                extern_context=extern_context,
                extern_c_declarations=extern_c_declarations,
            ):
                decl_entity = extract_declaration_entity(
                    child,
                    source_bytes,
                    repo_name,
                    file_path,
                    namespace_stack,
                )
                if decl_entity:
                    entities.append(decl_entity)
        
        # Handle direct entity nodes
        elif child.type in TARGET_ENTITY_TYPES:
            entity = extract_entity_from_node(
                child, source_bytes, repo_name, file_path, namespace_stack
            )
            if entity:
                entities.append(entity)
        
        # Handle transparent wrappers (extern "C", etc.)
        elif child.type in TRANSPARENT_WRAPPERS:
            body = child.child_by_field_name("body")
            if body:
                entities.extend(
                    traverse_and_extract(
                        body,
                        source_bytes,
                        repo_name,
                        file_path,
                        namespace_stack,
                        include_declarations=include_declarations,
                        extern_c_declarations=extern_c_declarations,
                        extern_context=True,
                    )
                )
        
        # Handle preprocessor containers (#ifdef, #ifndef, etc.)
        elif child.type in PREPROCESSOR_CONTAINERS:
            # Preprocessor directives have children we need to traverse
            entities.extend(
                traverse_and_extract(
                    child,
                    source_bytes,
                    repo_name,
                    file_path,
                    namespace_stack,
                    include_declarations=include_declarations,
                    extern_c_declarations=extern_c_declarations,
                    extern_context=extern_context,
                )
            )
        
        # Handle other container types - recurse
        elif child.type in CONTAINER_TYPES:
            entities.extend(
                traverse_and_extract(
                    child,
                    source_bytes,
                    repo_name,
                    file_path,
                    namespace_stack,
                    include_declarations=include_declarations,
                    extern_c_declarations=extern_c_declarations,
                    extern_context=extern_context,
                )
            )
    
    return entities


def extract_entities_from_tree(
    tree: Tree,
    source_bytes: bytes,
    repo_name: str,
    file_path: str,
    include_declarations: bool = DEFAULT_INCLUDE_DECLARATIONS,
    extern_c_declarations: bool = DEFAULT_EXTERN_C_DECLARATIONS,
) -> List[ExtractedEntity]:
    """Extract all entities from a parsed C++ AST.
    
    This is the main entry point for entity extraction.
    
    Args:
        tree: The parsed AST tree.
        source_bytes: The raw source file bytes.
        repo_name: Repository name for URI generation.
        file_path: File path relative to repo root.
        include_declarations: Whether to extract declaration-only functions.
        extern_c_declarations: Whether to include declaration-only functions
            inside extern "C" contexts.
        
    Returns:
        List of all extracted entities.
    """
    logger.info(f"Extracting entities from {file_path}")
    entities = traverse_and_extract(
        tree.root_node,
        source_bytes,
        repo_name,
        file_path,
        include_declarations=include_declarations,
        extern_c_declarations=extern_c_declarations,
    )
    _disambiguate_overloaded_function_uris(entities)
    logger.info(f"Extracted {len(entities)} entities from {file_path}")
    return entities
