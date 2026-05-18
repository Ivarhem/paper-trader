#!/usr/bin/env python3
from __future__ import annotations
import ast, json, py_compile, re, sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.agents.lib.agent_contract import write_json_shared
try:
    import yaml
except Exception:  # pragma: no cover - smoke output reports missing optional parser
    yaml = None

SCHEDULED_AGENT_PATHS = [
    "tools/agents/universe_discovery_agent.py",
    "tools/agents/daily_price_refresh_agent.py",
    "tools/agents/opendart_disclosure_agent.py",
    "tools/agents/sec_edgar_disclosure_agent.py",
    "tools/agents/universe_curator.py",
    "tools/agents/opendart_financial_agent.py",
    "tools/agents/data_quality_agent.py",
    "tools/agents/strategy_generator_agent.py",
    "tools/agents/validation_capacity_planner.py",
    "tools/agents/simulation_validation_worker.py",
    "tools/agents/strategy_novelty_pruner.py",
    "tools/agents/strategy_lifecycle_agent.py",
    "tools/agents/active_strategy_balancer_agent.py",
    "tools/agents/strategy_success_optimizer_agent.py",
    "tools/agents/recommendation_agent.py",
    "tools/agents/recommendation_critic_agent.py",
    "tools/agents/portfolio_risk_manager_agent.py",
    "tools/agents/market_regime_gate_agent.py",
    "tools/agents/investment_committee_agent.py",
    "tools/agents/current_recommendation_validation_worker.py",
    "tools/agents/committee_performance_ledger_agent.py",
    "tools/agents/recommendation_outcome_tracker_agent.py",
    "tools/agents/recommendation_funnel_agent.py",
    "tools/agents/recommendation_calibration_agent.py",
    "tools/agents/recommendation_auditor.py",
    "tools/agents/outcome_attribution_agent.py",
    "tools/agents/org_evaluator_agent.py",
    "tools/agents/org_improvement_guardian_agent.py",
    "tools/agents/stock_research_run.py",
    "tools/agents/strategy_tail_risk_filter_agent.py",
    "tools/agents/discovery_validation_worker.py",
    "tools/agents/disclosure_impact_agent.py",
    "tools/agents/market_context_agent.py",
    "tools/agents/market_issue_scout_agent.py",
    "tools/agents/market_news_issue_scout_agent.py",
    "tools/agents/market_issue_narrative_agent.py",
    "tools/agents/oversold_recovery_agent.py",
    "tools/agents/shadow_recommendation_agent.py",
    "tools/agents/internal_signal_board_agent.py",
    "tools/agents/paper_trader_integrity_agent.py",
    "tools/agents/exit_policy_optimizer_agent.py",
    "tools/agents/research_hypothesis_agent.py",
    "tools/agents/experiment_planner_agent.py",
    "tools/agents/experiment_runner_agent.py",
    "tools/agents/evidence_judge_agent.py",
    "tools/agents/research_experiment_ledger_agent.py",
    "tools/agents/research_org_orchestrator.py",
]
PACKET_NAMES = ("packet", "report", "payload")
ASSIGN_RE = {name: re.compile(rf"^\s*{name}\s*=") for name in PACKET_NAMES}
READ_RE = {name: re.compile(rf"\b{name}\s*\[") for name in PACKET_NAMES}




def scheduled_agent_paths() -> list[str]:
    paths=set(SCHEDULED_AGENT_PATHS)
    pipeline=ROOT / "tools" / "agents" / "research_pipeline_agent.py"
    if pipeline.exists():
        text=pipeline.read_text(encoding="utf-8")
        for match in re.findall(r"['\"](tools/agents/[^'\"]+\.py)['\"]", text):
            paths.add(match)
    return sorted(paths)

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def find_packet_reads_before_assignment(path: Path) -> list[dict]:
    """Catch the historical cron breaker: constructing JSON while reading packet['x'] before packet exists.

    This deliberately avoids broad linting to keep scheduled runs low-noise. It only flags direct
    subscript reads of packet/report/payload before a same-function assignment to that object.
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    lines = source.splitlines()
    problems: list[dict] = []
    for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        start = fn.lineno
        end = getattr(fn, "end_lineno", start)
        for name in PACKET_NAMES:
            first_assign = None
            for lineno in range(start, end + 1):
                if ASSIGN_RE[name].search(lines[lineno - 1]):
                    first_assign = lineno
                    break
            if first_assign is None:
                continue
            for lineno in range(start, first_assign):
                line = lines[lineno - 1]
                if READ_RE[name].search(line):
                    problems.append({"file": str(path.relative_to(ROOT)), "function": fn.name, "name": name, "line": lineno, "before_assignment_line": first_assign})
    return problems


def has_main_guard(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    return "if __name__" in text and "main()" in text


def validate_research_config() -> dict:
    problems: list[dict] = []
    warnings: list[dict] = []
    agents_path = ROOT / "configs" / "research_agents.yaml"
    pipeline_path = ROOT / "configs" / "research_pipeline.yaml"
    if yaml is None:
        return {
            "problems": [{"type": "missing_yaml_parser", "detail": "PyYAML is required to validate research_agents.yaml"}],
            "warnings": warnings,
        }
    try:
        agents = yaml.safe_load(agents_path.read_text(encoding="utf-8")) or {}
        pipeline = json.loads(pipeline_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"problems": [{"type": "config_parse_error", "error": str(exc)}], "warnings": warnings}

    contracts = agents.get("key_agent_contracts") or {}
    for name, contract in contracts.items():
        script = contract.get("script")
        if script and not (ROOT / script).exists():
            problems.append({"type": "missing_contract_script", "agent": name, "script": script})

    group_pairs: list[tuple[str, str]] = []
    for group, data in (agents.get("agent_groups") or {}).items():
        for agent in data.get("agents") or []:
            group_pairs.append((agent, group))
    group_counts = Counter(agent for agent, _ in group_pairs)
    for agent, count in sorted(group_counts.items()):
        if count > 1:
            warnings.append({
                "type": "duplicate_group_agent",
                "agent": agent,
                "groups": [group for item, group in group_pairs if item == agent],
            })

    pipeline_agents = set((pipeline.get("agents") or {}).keys())
    grouped_agents = {agent for agent, _ in group_pairs}
    for agent in sorted(grouped_agents - pipeline_agents):
        problems.append({"type": "group_agent_missing_pipeline_policy", "agent": agent})
    for agent in sorted(pipeline_agents - grouped_agents):
        warnings.append({"type": "pipeline_policy_without_group", "agent": agent})

    return {"problems": problems, "warnings": warnings}


def main() -> int:
    output = Path("/tmp/pipeline_smoke_check_latest.json")
    compile_errors = []
    packet_errors = []
    guard_warnings = []
    config_validation = validate_research_config()
    scheduled_paths = scheduled_agent_paths()
    for rel in scheduled_paths:
        path = ROOT / rel
        try:
            py_compile.compile(str(path), doraise=True)
        except Exception as exc:
            compile_errors.append({"file": rel, "error": str(exc)})
            continue
        packet_errors.extend(find_packet_reads_before_assignment(path))
        if not has_main_guard(path):
            guard_warnings.append({"file": rel, "warning": "missing conventional main guard"})
    warnings = []
    if packet_errors:
        warnings.append("Potential JSON packet/report/payload read before assignment in scheduled agents.")
    if compile_errors:
        warnings.append("Scheduled agent py_compile failure.")
    if guard_warnings:
        warnings.append("Some scheduled agents are missing conventional main guards.")
    if config_validation["problems"]:
        warnings.append("Research config validation failed.")
    if config_validation["warnings"]:
        warnings.append("Research config validation warnings present.")
    report = {
        "run_at": utc_now(),
        "mode": "scheduled_agent_smoke_check",
        "real_trading": False,
        "contract": {
            "status": "ok" if not compile_errors and not packet_errors and not config_validation["problems"] else "failed",
            "metrics": {
                "checked_agents": len(scheduled_paths),
                "compile_errors": len(compile_errors),
                "packet_reads_before_assignment": len(packet_errors),
                "main_guard_warnings": len(guard_warnings),
                "config_validation_problems": len(config_validation["problems"]),
                "config_validation_warnings": len(config_validation["warnings"]),
            },
            "warnings": warnings,
        },
        "config_validation": config_validation,
        "compile_errors": compile_errors,
        "packet_reads_before_assignment": packet_errors,
        "main_guard_warnings": guard_warnings,
    }
    write_json_shared(output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if compile_errors or packet_errors or config_validation["problems"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
