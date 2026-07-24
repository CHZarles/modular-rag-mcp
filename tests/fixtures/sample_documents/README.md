# Sample documents (fixtures)

These files exist only so that downstream tests have a stable, version-controlled
placeholder to ingest when no real PDF is available. They are **not** real
documents — they are minimal stubs that downstream ingestion tests can
override or replace with their own fixtures.

| File              | Purpose                                                |
| ----------------- | ------------------------------------------------------ |
| `placeholder.txt` | Empty placeholder used by A2 / C2 file-integrity tests |

Adding a real PDF for end-to-end testing belongs to stage C (`tests/fixtures/sample_documents/*.pdf`), not here.