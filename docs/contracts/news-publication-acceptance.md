# News publication acceptance / PRD extension

## Goal and scope

Publish evidence-backed news analysis only after independent deterministic
acceptance. Both `market_news_issue_scout_agent.py` and
`market_issue_narrative_agent.py` now register a run before collection, capture a
clean Git HEAD and destination baselines, and call the shared publication gate.
This extends the current codebase; it is not a claim that older operational PRD
items, the production deployment, or investment performance have been verified.

| ID | Acceptance requirement | Evidence |
| --- | --- | --- |
| NEWS-001 | Evidence-backed issues carry HTTP(S) URL and nonempty title references | Per-source findings; captured packet hash |
| NEWS-002 | Explicitly retracted or superseded sources block publication pending reanalysis | `retracted` / `superseded_by` findings |
| NEWS-003 | Issue IDs are present and unique; declared count matches | Candidate-level deterministic checks |
| NEWS-004 | Completed, explicit paper-only analysis is required | Contract status, mode, nonempty issues |
| NEWS-005 | Pin the run to a committed revision and unchanged destinations | Run ID, Git SHA, DB/file baseline hashes |
| NEWS-006 | Cancellation, duplicate callbacks and stale competing runs cannot publish | Transactional run status and baseline fencing |

## Runtime behavior

`news_publication_runs` holds the run ID, revision, status, immutable serialized
candidates, candidate hashes (in reports), requirement-linked findings and
timestamps. It is separate from `latest_artifacts`, the canonical published view.
The pure evaluator in `news_acceptance.py` has no DB/network dependency and never
alters inputs. Evaluating a snapshot twice produces the same report. Publication,
not evaluation, records the report and updates the latest view.

Validation failure exits nonzero, stores the blocked candidates/reports, and
preserves the previous publication. The pipeline already records nonzero agent
exits; the run ID is emitted to stderr at startup and appears in pipeline logs.
An empty analysis is deliberately blocked rather than erasing a good prior view;
that prior view retains its original `run_at` and must not be represented as fresh.
Heuristic-only narrative rows may lack web sources, but remain explicitly
`heuristic_only`. This is not evidence-backed causal analysis.

`BEGIN IMMEDIATE` serializes cancellation and publication. If cancellation wins,
late callbacks cannot mutate either published content or run evidence. If a
publication has already committed, cancellation returns false; it does not retract
published content. A run can publish only once. Competing runs that captured the
same baseline cannot overwrite each other. Changes to a legacy input file also
invalidate a pending publication.

Both narrative destinations pass before either DB row is promoted. File mirrors
are individually replace-written under the same writer lock. They are **not** a
transactional multi-file read surface. On ordinary mirror-write errors, earlier
mirrors are restored and the DB transaction rolls back. A process/power failure
between file replacement and DB commit can leave mismatched mirrors: stop the
affected agents and reconcile from the canonical DB before restarting legacy
file readers. A source-control change during a run blocks publication. Deploy a
clean committed checkout, not a tree with work in progress.

## Operator commands

Use the same `DATABASE_PATH` / `.env` as the target deployment. Run from repo root:

```sh
python scripts/news_publication.py status RUN_ID
python scripts/news_publication.py cancel RUN_ID
python scripts/news_publication.py reconcile RUN_ID
python scripts/news_publication.py evaluate captured_packet.json --run-id RUN_ID --commit FULL_GIT_SHA
```

`status` opens SQLite read-only without database seeding. `evaluate` reads only
the supplied JSON and writes its report to stdout; exit 0 means pass, 1 means
block. `reconcile` always restores the current canonical latest state, never a
superseded run's snapshot. Do not sync unaccepted candidate files through the
legacy generic artifact sync helper.

## Verification and limits

```sh
python -m unittest discover -s tests -v
```

The committed fixture set contains 10 synthetic normal and 10 synthetic error
cases, with repeated scoring/input immutability checks. Integration tests exercise
both actual agent entry points without network access, preserve-last-good,
cancelled callbacks, duplicate/stale runs, changed revisions/inputs, multi-target
validation, mirror-write rollback and repair. These are visible regression cases,
**not** an independently collected held-out operating sample. Before rollout,
freeze the implementation commit and evaluate new historical cases unavailable
during development: at least 10 normal and 10 faulty cases. Target: block all
faulty cases, pass at least 9 normal cases, identical repeat scores, and no
analysis/view changes during scoring.

URL/title validation does not prove source truth, freshness, symbol identity, or
causality. Corrections can only be rejected if the upstream record explicitly
marks them; the current RSS collectors do not discover all retractions. Semantic
duplicate events with different IDs are not detected. Other agents and generic
artifact writers remain outside this boundary. No claim of production reliability
or profitable trading follows from these tests. Deployment and live-data
acceptance require the actual Ubuntu runtime and should use a staged copy rather
than directly editing `/service/services/paper_trader`.
