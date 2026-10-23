# News evidence selection: shadow experiment / PRD extension

## Goal

Separate collection from analysis-input selection, preserving claim-linked
evidence, source versions and explicit correction/counterargument markers. This
is deterministic extractive selection inspired by the separation principle in
RPL; it does not implement the paper's RL training or claim its measured gains.

The module and comparison command are **shadow-only**. They make no LLM/network
calls, do not initialize or change a DB, and never promote results into
`latest_artifacts` or the live analysis pipeline. No existing agents or cron
schedules are changed. Production use is conditional on independent historical
acceptance and subsequent deployment; neither follows from synthetic tests.

| Requirement | Implementation / evidence |
| --- | --- |
| EVID-001: Historical cutoff | Explicit timezone-aware `published_at`, `available_at`, `metadata_as_of`; reject unknown/late versions |
| EVID-002: Traceable evidence | Immutable source ID, document hash, exact quote offsets, per-claim evidence IDs |
| EVID-003: Protect corrections and rebuttals | Preserve explicitly tagged documents and detected correction/rebuttal sentences; block if they exceed budget |
| EVID-004: Same corpus in both arms | Full and selected contexts derive from the same eligible captured versions |
| EVID-005: Honest comparison | Named optional tokenizer; unavailable token counts remain null; no character-to-token estimate |
| EVID-006: Conditional rollout | At least 20 historical cases with human-declared nonempty gold, 30% aggregate input-token reduction, 95% gold retention, no added correction/rebuttal omissions |

## Input contract

Single selection takes one captured case; `--compare` takes a JSON array of cases.
An example case below is only a schema example, not a historical observation:

```json
{
  "case_id": "example-only",
  "dataset_kind": "synthetic",
  "cutoff": "2026-09-01T23:59:00+09:00",
  "claims": [{"claim_id": "demand", "text": "수요 전망 확인", "keywords": ["수요"]}],
  "documents": [{
    "source_id": "immutable-source-version-1",
    "url": "https://example.org/article",
    "title": "합성 예제",
    "published_at": "2026-09-01T08:00:00+09:00",
    "available_at": "2026-09-01T08:10:00+09:00",
    "metadata_as_of": "2026-09-01T08:10:00+09:00",
    "stance": "correction",
    "text": "수요 전망은 정정되었다."
  }],
  "annotation": {"origin": "synthetic", "reviewer": "example-generator"},
  "required_evidence": [{"source_id": "immutable-source-version-1", "quote": "수요 전망은 정정되었다.", "kind": "correction"}]
}
```

`available_at` means this **exact archived content version** was demonstrably
available at that time. It must not be guessed from the article's publication
date. `metadata_as_of` applies to stance, retraction and supersession metadata;
if omitted, it asserts that the entire record was known at `available_at`.
Later content updates need new source IDs and availability times. Missing,
timezone-less and date-only times are excluded. This intentionally prevents
current RSS titles with guessed dates from masquerading as historical snapshots.

Stance values are `support`, `history`, `neutral`, `correction`, `counterargument`
and `retracted`. Optional `retracted: true` and `superseded_by: SOURCE_ID` preserve
invalidation context. Retracted passages remain labelled as such for analysis;
they are not valid support for an investment claim. The selector cannot discover
unknown retractions, certify stance labels, authenticate a human annotation or
establish causation. Relevance is lexical, not a model's factual judgment.

The selector preserves all protected spans, then covers each claim with its best
matching passage, then uses remaining character budget for ranked passages.
Passages are exact extracts, not summaries. Unresolved claims or mandatory spans
exceeding the budget return `blocked` with diagnostic context, never permission
to publish. Source IDs cannot refer to conflicting versions. Input, output and
implementation hashes support replay and review.

## Run and verify

```sh
python -m unittest discover -s tests -v
python tests/fixtures/build_news_evidence_cases.py > /tmp/news-evidence-cases.json
python tools/agents/news_evidence_selection_agent.py \
  --input /tmp/news-evidence-cases.json --compare --max-context-chars 6000 \
  --output /tmp/news-evidence-shadow-report.json
```

The example comparison exits 2 because its data is synthetic and its required
evidence was generated, not marked by a human. Selection exit 0 only means an
input is ready for comparison. Comparison exit 0 means the supplied dataset
meets declared criteria, **not** that production was changed. Existing output
files and `*_latest.json` destinations are refused. Reports do not contain an
entire copy of the raw corpus; individual selection output contains the two
contexts for downstream review.

For exact token counts, install optional `tiktoken` in the evaluation environment
and pass `--tokenizer ENCODING`. Record the encoding for the intended downstream
model: `cl100k_base` is a reference encoding, not a claim about Claude's actual
token usage. Counts cover serialized JSON context only, not provider chat framing,
system instructions, output tokens or downstream answer quality. Without an
encoding, character counts are reported separately and the token criterion is
unverified. The implementation hashes the CLI and both modules in each report.

## Current evidence and next gate

The fixture generator produces 20 explicitly synthetic scenarios. It uses long
repeated background text to exercise compression and exact quote preservation;
its compression result is not representative of real news. It contains future
and undated sources and known correction/rebuttal markers. Unit/integration tests
check verbatim extracts, immutable inputs, cutoff enforcement, protected-budget
failure, ID collisions, incomplete annotations and refusal to overwrite views.

No archived 20-case historical corpus or human-selected gold was available in
the checked-out repository or the Library searches for this request. The remote
Mac device was offline. Do not mark historical acceptance complete. Once archived
cases are accessible, freeze implementation, capture their exact availability
evidence, have independent reviewers specify required quotes, and compare the
same corpus under the intended model tokenizer. Check per-case losses as well
as aggregate metrics; high average recall can hide one badly damaged case.

Only after that gate passes should an explicitly reviewed integration put
`selected_context` into the analysis stage. Keep raw collection, rejected
candidates and comparison reports outside the published view. Continue using
the independent publication gate for completed analysis. The earlier publication
gate is a separate, still-unverified production deployment, not evidence this
new module is active.
