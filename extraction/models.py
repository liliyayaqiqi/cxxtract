"""
Data models for extracted C++ entities.
"""

from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any

from core.uri_contract import create_global_uri


@dataclass
class ExtractedEntity:
    """Represents a single extracted C++ entity (class, struct, or function).
    
    Attributes:
        global_uri: Unique identifier in format RepoName::FilePath::EntityType::EntityName
        repo_name: Repository name
        file_path: Relative path from repository root
        entity_type: One of: Class, Struct, Function
        entity_name: Qualified name (e.g., MyNamespace::MyClass)
        docstring: Concatenated Doxygen comment text, or None
        code_text: Full source text of the entity (including template prefix)
        start_line: 1-indexed start line in the file
        end_line: 1-indexed end line in the file
        is_templated: Whether the entity is wrapped in template_declaration
    """
    
    global_uri: str
    repo_name: str
    file_path: str
    entity_type: str
    entity_name: str
    docstring: Optional[str]
    code_text: str
    start_line: int
    end_line: int
    is_templated: bool
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert the entity to a dictionary suitable for JSON serialization.
        
        Returns:
            Dictionary representation of the entity.
        """
        return asdict(self)
    
    @classmethod
    def create_uri(
        cls,
        repo_name: str,
        file_path: str,
        entity_type: str,
        entity_name: str,
        function_signature: str | None = None,
        function_sig_hash: str | None = None,
    ) -> str:
        """Build a Global URI from components.
        
        Args:
            repo_name: Repository name
            file_path: File path relative to repo root
            entity_type: Entity type (Class, Struct, Function)
            entity_name: Qualified entity name
            function_signature: Optional function signature text.
            function_sig_hash: Optional precomputed function signature hash.
            
        Returns:
            Global URI string in format RepoName::FilePath::EntityType::EntityName
        """
        return create_global_uri(
            repo_name=repo_name,
            file_path=file_path,
            entity_type=entity_type,
            entity_name=entity_name,
            function_signature=function_signature,
            function_sig_hash=function_sig_hash,
        )
