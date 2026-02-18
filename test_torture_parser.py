#!/usr/bin/env python3
"""
Test script to parse test_torture.h and display extracted entities.
"""

import json
from extraction.extractor import extract_file

# Parse the torture test file
file_path = "extraction/tests/fixtures/test_torture.h"
repo_name = "rtc_engine"

print("Parsing test_torture.h...")
print("=" * 80)
print()

entities = extract_file(file_path, repo_name)

print(f"Found {len(entities)} entities:\n")

for i, entity in enumerate(entities, 1):
    print(f"Entity #{i}")
    print("-" * 80)
    
    # Create dict with only the requested fields
    output = {
        "global_uri": entity.global_uri,
        "entity_type": entity.entity_type,
        "docstring": entity.docstring
    }
    
    print(json.dumps(output, indent=2, ensure_ascii=False))
    print()
