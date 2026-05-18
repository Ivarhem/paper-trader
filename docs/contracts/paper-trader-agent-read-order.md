# paper_trader Agent Read Order Contract

Purpose: keep the pipeline harness thin. Agents and UI diagnostics should read
small contract artifacts first and open full debug payloads only when those
contracts show a concrete reason.

## Default Read Order

1. /tmp/research_pipeline_status.json
2. /tmp/research_org_suborg_summary_latest.json
3. /tmp/research_queue_latest.json
4. /tmp/context_goal_latest.json
5. /tmp/recommendations_status_latest.json
6. /tmp/audit_status_latest.json
7. Agent-run DB rows or paged API responses
8. Full artifacts such as /tmp/research_pipeline_latest.json,
   /tmp/recommendations_latest.json, and
   /tmp/recommendation_audit_full_latest.json

## Routing Rules

- Pipeline health: start with /tmp/research_pipeline_status.json.
- Recommendation display/drift: start with /tmp/recommendations_status_latest.json.
- Strategy trust/audit quality: start with /tmp/audit_status_latest.json.
- Department ownership or repeated agent questions: start with
  /tmp/research_org_suborg_summary_latest.json.
- UI missing-data diagnosis: check the API/static compact artifact before opening
  the full /tmp payload.
- Open full artifacts only for targeted debugging, schema repair, or when compact
  artifacts disagree with the UI/API result.

## Harness Boundary

tools/agents/research_pipeline_agent.py should remain an orchestrator:
run agents, collect step status, write report artifacts, and exit with the
right status. Domain-facing compact contracts belong in
tools/agents/lib/pipeline_context_contracts.py or more specific diagnostic
agents.
