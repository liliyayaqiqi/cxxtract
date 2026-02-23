# Synthetic SCIP Fixtures

These fixtures are intentionally small, deterministic `.scip` indexes used for
Phase 0 baseline and quality-gate tests.

Files:
- `basic_graph.scip`: class/method/function definitions with one reference edge.
- `nested_scope.scip`: nested definition ranges used to validate enclosing-scope attribution.

Regenerate:

```bash
python -m graphrag.tests.fixtures.build_scip_fixtures
```

