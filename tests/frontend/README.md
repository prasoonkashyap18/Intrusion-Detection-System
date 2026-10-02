# Frontend tests

Frontend tests live in [`frontend/tests/`](../../frontend/tests), not here.

Node resolves packages by walking up from the importing file, so a test outside
`frontend/` cannot reach that package's `node_modules`. Keeping the suite inside
the package it tests is also the conventional layout for a JavaScript project.

Run them from `frontend/`:

```bash
npm test
```

The Python suites (`tests/backend/`, `tests/ml/`) stay here — pytest resolves
imports through `conftest.py` instead.
