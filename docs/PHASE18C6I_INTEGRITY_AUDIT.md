# Phase 18C6I: integrity audit

`integrity_audit.py` audits an offline SQLite copy without importing the application or
performing repairs.

## Usage

```text
python integrity_audit.py path/to/offline-copy.db
python integrity_audit.py path/to/offline-copy.db --json
python integrity_audit.py path/to/offline-copy.db --preflight
```

The input must be a checkpointed rollback-mode SQLite file. The auditor refuses a WAL,
SHM, or journal sidecar and opens the database with `mode=ro`.

## Results and exit codes

The text report prints only non-PASS examples and a summary. `--json` prints the same
result as a machine-readable object with `status`, `summary`, `checks`, `results`, and
`by_code`.

- `PASS`: no finding was produced; exit `0`.
- `WARN`: non-blocking evidence-quality or historical finding; exit `0`.
- `FAIL`: a blocking integrity finding; exit `1`.
- `ERROR`: inaccessible, invalid, incomplete, or unsafe input; exit `2`.

Each check is represented in `results`, including zero-count PASS checks. Examples are
limited to five per check.

## Checks

The audit covers:

- schema coverage and SQLite integrity;
- foreign keys and conventional missing-ID references;
- relation endpoints, cross-concept and self relations, reversed pairs, duplicates,
  unknown phonological parameters, and cross-concept components;
- current-state uniqueness and invalid current flags for assignments, grammar,
  morphology, concept references, submissions, and catalog media;
- retired assignment and media targets;
- source and occurrence temporal values, retired sources, negative media sizes, and
  invalid canonical YouTube video assets;
- orphan occurrence/alternative media references and current canonical-video state;
- nomenclature rules shared with A.3, including labels, components, chronology, `a`,
  capacity, connectivity, and canonical-map compatibility;
- concept and alternative references, closed submission result requirements, decision
  state, assignment result consistency, and materialized grammar/lexical results;
- renumber-event/change references and event/change concept consistency;
- known polymorphic activity-event references;
- publication snapshot structure and SHA256 integrity.

A different `occurrence_concept_reference` is not compared with an assignment's concept:
that reference is allowed to represent a distinct analytical concept.

## Preflight

`--preflight` runs the same rules and only omits deliberately non-blocking checks. It
does not use a separate integrity policy. WARN-only findings therefore return `0`, while
any FAIL returns `1`; access and usage errors still return `2`.

## Deliberate warnings and limitations

- An active alternative without a current assignment is `WARN`, because incomplete
  evidence is not necessarily structural corruption.
- An active alternative with no evidence is also `WARN`.
- A valid current nomenclature map that differs from the calculated automatic labels is
  `WARN`; manual, historically justified labels are not rewritten by the auditor.
- Accepted grammar submissions without a materialized history record and closed lexical
  submissions without a decision record are `WARN` for legacy/backfilled databases.
- Activity events with an unknown polymorphic `entity_type` are `WARN` because the
  auditor cannot infer a target table. Known entity types with missing targets are
  `FAIL`.
- Historical publication snapshots are validated internally; they are not required to
  equal the current catalog state.
- The auditor reports findings only. It does not repair data, create migrations, use a
  production database, or connect to Railway.

## Read-only guarantee

The database is checked before opening. The connection uses the SQLite `mode=ro` URI,
`query_only=ON`, a restrictive authorizer, and a read transaction. Writes, schema
changes, attachments, unsafe pragmas, extensions, WAL/journal sidecars, and invalid
SQLite files are rejected. Tests verify SHA256 bytes, logical dumps, directory sidecars,
and `total_changes == 0` for both valid and invalid audit outcomes. All test databases
are temporary fixtures.
