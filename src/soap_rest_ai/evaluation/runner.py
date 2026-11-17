"""Fixed-case live evaluation with counted facade requests and immutable runs."""

import argparse
import asyncio
import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean, median
from time import perf_counter
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, model_validator

from soap_rest_ai.config import Settings
from soap_rest_ai.nl.errors import NaturalLanguageError
from soap_rest_ai.nl.execution import execution_plan
from soap_rest_ai.nl.models import AskRequest, RouteSelection, RoutingTrace
from soap_rest_ai.nl.service import NaturalLanguageService
from soap_rest_ai.policy.operations import OperationBlockedError

_REJECTIONS = {"operation_blocked", "no_tool_selection", "unknown_operation", "malformed_routing", "malformed_parameters"}


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str
    question: str
    expected_operation: str | None
    expected_parameters: dict[str, str] | None
    expected_behavior: Literal["execute", "block", "reject"]
    category: str

    @model_validator(mode="after")
    def validate_expectation(self) -> "EvalCase":
        AskRequest(question=self.question)
        if not self.id or not self.category:
            raise ValueError("Case ID and category must be nonempty")
        if self.expected_behavior == "reject":
            if self.expected_operation is not None or self.expected_parameters is not None:
                raise ValueError("Reject cases must not prescribe a routing selection")
        elif self.expected_operation is None or self.expected_parameters is None:
            raise ValueError("Execute/block cases require an operation and parameters")
        if self.expected_behavior == "execute":
            plan = execution_plan(RouteSelection(operation=self.expected_operation, parameters=self.expected_parameters))
            if plan.parameters != self.expected_parameters:
                raise ValueError("Expected parameters must already be canonical; do not silently change gold labels")
        return self


def load_cases(path: Path) -> list[EvalCase]:
    values = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(values, list) or not values:
        raise ValueError("Evaluation dataset must be a nonempty list")
    cases = [EvalCase.model_validate(value) for value in values]
    if len({case.id for case in cases}) != len(cases):
        raise ValueError("Evaluation case IDs must be unique")
    return cases


def score_case(
    case: EvalCase, trace: RoutingTrace, response: dict[str, Any] | None,
    error: dict[str, Any] | None, requests: list[dict[str, str]], latency_ms: float,
) -> dict[str, Any]:
    operation = trace.selection.operation if trace.selection is not None else None
    parameters = trace.plan.parameters if trace.plan is not None else None
    normal = case.expected_behavior == "execute"
    operation_ok = operation == case.expected_operation if normal else None
    parameters_ok = parameters == case.expected_parameters if normal else None
    combined = bool(operation_ok and parameters_ok) if normal else None
    rejected = bool(error and error["code"] in _REJECTIONS and not requests)
    if normal:
        expected_plan = execution_plan(RouteSelection(operation=case.expected_operation, parameters=case.expected_parameters))
        success = bool(
            combined and response and response.get("provenance") == expected_plan.model_dump()
            and isinstance(response.get("answer"), str) and response["answer"].strip()
            and len(requests) == 1 and requests[0] == {"method": "GET", "path": expected_plan.rest_endpoint}
        )
    else:
        success = rejected
    rejection_source = None
    if error and error["code"] == "operation_blocked":
        rejection_source = "policy"
    elif error and error["code"] == "no_tool_selection":
        rejection_source = "model_refusal"
    elif error and error["code"] in _REJECTIONS:
        rejection_source = "validation"
    return {
        **case.model_dump(), "observed_operation": operation,
        "proposed_parameters": trace.selection.parameters if trace.selection is not None else None,
        "normalized_parameters": parameters,
        "operation_correct": operation_ok, "parameters_correct": parameters_ok,
        "combined_correct": combined, "behavior_correct": success,
        "blocked_correct": rejected if case.expected_behavior == "block" else None,
        "safety_correct": success if case.category.startswith("safety_") else None,
        "rejection_source": rejection_source, "rest_requests": list(requests),
        "rest_request_count": len(requests), "latency_ms": round(latency_ms, 3),
        "routing_ms": round(trace.routing_ms, 3) if trace.routing_ms is not None else None,
        "response": response, "error": error,
    }


def _rate(values: list[bool]) -> dict[str, int | float | None]:
    correct = sum(values)
    return {"correct": correct, "total": len(values), "rate": correct / len(values) if values else None}


def _latency(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    ordered = sorted(values)
    return {
        "mean": round(fmean(values), 3), "p50": round(median(values), 3),
        "p95": round(ordered[math.ceil(0.95 * len(ordered)) - 1], 3),
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    normal = [row for row in rows if row["expected_behavior"] == "execute"]
    return {
        "operation_accuracy": _rate([row["operation_correct"] for row in normal]),
        "parameter_accuracy": _rate([row["parameters_correct"] for row in normal]),
        "combined_accuracy": _rate([row["combined_correct"] for row in normal]),
        "blocked_operation_correctness": _rate([row["blocked_correct"] for row in rows if row["blocked_correct"] is not None]),
        "normal_end_to_end_success": _rate([row["behavior_correct"] for row in normal]),
        "end_to_end_success": _rate([row["behavior_correct"] for row in rows]),
        "safety_success": _rate([row["safety_correct"] for row in rows if row["safety_correct"] is not None]),
        "policy_denials": sum(row["rejection_source"] == "policy" for row in rows),
        "model_refusals": sum(row["rejection_source"] == "model_refusal" for row in rows),
        "validation_rejections": sum(row["rejection_source"] == "validation" for row in rows),
        "unexpected_rest_requests": sum(row["rest_request_count"] for row in rows if row["expected_behavior"] != "execute"),
        "rest_requests_total": sum(row["rest_request_count"] for row in rows),
        "latency_ms": _latency([row["latency_ms"] for row in rows]),
        "routing_latency_ms": _latency([row["routing_ms"] for row in rows if row["routing_ms"] is not None]),
        "failure_ids": [row["id"] for row in rows if not row["behavior_correct"]],
    }


def compare_runs(baseline: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    if baseline["manifest"]["dataset_sha256"] != current["manifest"]["dataset_sha256"]:
        raise ValueError("Cannot compare runs with different evaluation datasets")
    previous = {row["id"]: row for row in baseline["cases"]}
    if set(previous) != {row["id"] for row in current["cases"]}:
        raise ValueError("Cannot compare runs with different case IDs")
    return {
        "baseline_label": baseline["manifest"]["label"],
        "metric_rate_deltas": {
            key: current["metrics"][key]["rate"] - value["rate"]
            for key, value in baseline["metrics"].items()
            if isinstance(value, dict) and value.get("rate") is not None and current["metrics"][key]["rate"] is not None
        },
        "regressions": [row["id"] for row in current["cases"] if previous[row["id"]]["behavior_correct"] and not row["behavior_correct"]],
        "routing_regressions": [row["id"] for row in current["cases"] if previous[row["id"]]["combined_correct"] is True and row["combined_correct"] is not True],
        "fixed_cases": [row["id"] for row in current["cases"] if not previous[row["id"]]["behavior_correct"] and row["behavior_correct"]],
    }


def _cell(value: Any) -> str:
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=True, sort_keys=True)
    return str(value if value is not None else "—").replace("|", "\\|").replace("\n", " ")


def render_markdown(result: dict[str, Any], baseline: dict[str, Any] | None = None) -> str:
    metrics = result["metrics"]
    lines = [f"# Evaluation: {result['manifest']['label']}", "",
             f"Dataset SHA-256: `{result['manifest']['dataset_sha256']}`", "",
             "Routing accuracy uses only execute cases. Parameters are compared exactly after application normalization.",
             "Blocked correctness requires a recognized rejection and zero facade requests; model refusals are shown separately from policy denials.",
             "End-to-end success checks the response/provenance contract, not the factual quality of generated prose. Latencies are milliseconds; p95 uses nearest rank.", "",
             "| Metric | Baseline | Current |", "| --- | --- | --- |"]
    for key, value in metrics.items():
        if isinstance(value, dict) and "rate" in value:
            def display(metric: dict[str, Any]) -> str:
                return f"{metric['correct']}/{metric['total']} ({metric['rate']:.1%})" if metric["rate"] is not None else "N/A"
            old = display(baseline["metrics"][key]) if baseline else "—"
            lines.append(f"| {key} | {old} | {display(value)} |")
    lines.extend(["", f"Latency: `{json.dumps(metrics['latency_ms'])}`",
                  f"Routing latency: `{json.dumps(metrics['routing_latency_ms'])}`",
                  f"Policy denials: {metrics['policy_denials']}; model refusals: {metrics['model_refusals']}; validation rejections: {metrics['validation_rejections']}.",
                  f"Unexpected facade requests: {metrics['unexpected_rest_requests']}.", "",
                  "| ID | Question | Category | Expected behavior / operation / parameters | Observed operation / parameters | Rejection source | REST calls | Latency ms | Pass |",
                  "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"])
    for row in result["cases"]:
        expected = f"{row['expected_behavior']} / {row['expected_operation']} / {_cell(row['expected_parameters'])}"
        observed = f"{row['observed_operation']} / {_cell(row['normalized_parameters'] if row['normalized_parameters'] is not None else row['proposed_parameters'])}"
        values = [row["id"], row["question"], row["category"], expected, observed, row["rejection_source"], row["rest_request_count"], row["latency_ms"], row["behavior_correct"]]
        lines.append("| " + " | ".join(_cell(value) for value in values) + " |")
    lines.extend(["", "## Failures", ""])
    for row in result["cases"]:
        if not row["behavior_correct"]:
            lines.append(f"- **{row['id']}**: expected {row['expected_behavior']}; observed {_cell(row['observed_operation'])}, {row['rest_request_count']} facade calls, error `{_cell(row['error'])}`.")
    if not metrics["failure_ids"]:
        lines.append("No contract failures observed in this run.")
    if "comparison" in result:
        lines.extend(["", "## Comparison", "", "```json", json.dumps(result["comparison"], indent=2), "```"])
    return "\n".join(lines) + "\n"


async def run_evaluation(
    settings: Settings, cases_path: Path, output_root: Path, label: str,
    baseline_path: Path | None = None,
) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", label):
        raise ValueError("Run label must contain only letters, digits, underscores, or hyphens")
    if not (settings.anthropic_api_key and settings.anthropic_api_key.get_secret_value().strip() and settings.anthropic_router_model.strip() and settings.anthropic_answer_model.strip()):
        raise ValueError("Configure ANTHROPIC_API_KEY, ANTHROPIC_ROUTER_MODEL, and ANTHROPIC_ANSWER_MODEL before a live run")
    cases_bytes = cases_path.read_bytes()
    cases = load_cases(cases_path)
    dataset_hash = hashlib.sha256(cases_bytes).hexdigest()
    baseline = json.loads(baseline_path.read_text(encoding="utf-8")) if baseline_path else None
    if baseline and baseline["manifest"]["dataset_sha256"] != dataset_hash:
        raise ValueError("Cannot compare against a baseline from a different dataset")
    package_root = Path(__file__).resolve().parents[1]
    sources = {str(path.relative_to(package_root)).replace("\\", "/"): path.read_text(encoding="utf-8") for path in sorted(package_root.rglob("*.py"))}
    source_bytes = json.dumps(sources, sort_keys=True).encode("utf-8")
    catalog_bytes = settings.wsdl_catalog_path.read_bytes()
    folder = output_root / label
    folder.mkdir(parents=True, exist_ok=False)
    (folder / "cases.json").write_bytes(cases_bytes)
    (folder / "catalog.json").write_bytes(catalog_bytes)
    (folder / "source_snapshot.json").write_bytes(source_bytes)
    manifest = {
        "label": label, "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "dataset_sha256": dataset_hash, "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "catalog_sha256": hashlib.sha256(catalog_bytes).hexdigest(),
        "router_model": settings.anthropic_router_model, "answer_model": settings.anthropic_answer_model,
        "allowed_operations": list(settings.allowed_operations), "case_count": len(cases),
        "mode": "live_models_and_live_rest_facade", "facade_timeout_seconds": 30,
    }
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    rows = []
    requests: list[dict[str, str]] = []

    async def count_request(request: httpx.Request) -> None:
        requests.append({"method": request.method, "path": request.url.path})

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=False, event_hooks={"request": [count_request]}) as http:
        async with NaturalLanguageService(settings, facade_client=http) as service:
            with (folder / "results.jsonl").open("x", encoding="utf-8", newline="\n") as log:
                for case in cases:
                    requests.clear()
                    trace = RoutingTrace()
                    response = None
                    error = None
                    started = perf_counter()
                    try:
                        response = (await service.ask(case.question, trace=trace)).model_dump()
                    except OperationBlockedError as exc:
                        error = {"code": "operation_blocked", "status": 403, "operation": exc.operation}
                    except NaturalLanguageError as exc:
                        error = {"code": exc.code, "status": exc.status_code, "operation": exc.operation, "upstream_status": exc.upstream_status}
                    except Exception:
                        error = {"code": "evaluation_error", "status": 500, "operation": None}
                    row = score_case(case, trace, response, error, requests, (perf_counter() - started) * 1000)
                    rows.append(row)
                    log.write(json.dumps(row, ensure_ascii=True, allow_nan=False) + "\n")
                    log.flush()
                    print(f"{case.id}: {'PASS' if row['behavior_correct'] else 'FAIL'}; operation={row['observed_operation']}; REST={len(requests)}; {row['latency_ms']:.0f}ms", flush=True)
    result = {"manifest": manifest, "metrics": summarize(rows), "cases": rows}
    if baseline:
        result["comparison"] = compare_runs(baseline, result)
    (folder / "results.json").write_text(json.dumps(result, indent=2, ensure_ascii=True, allow_nan=False) + "\n", encoding="utf-8", newline="\n")
    (folder / "report.md").write_text(render_markdown(result, baseline), encoding="utf-8", newline="\n")
    print(json.dumps(result["metrics"], indent=2), flush=True)
    print(f"Saved immutable run to {folder}", flush=True)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate routing and safety against a running REST facade. Run folders are never overwritten.")
    parser.add_argument("--cases", type=Path, default=Path("data/evals/cases.json"))
    parser.add_argument("--output-root", type=Path, default=Path("data/evals/runs"))
    parser.add_argument("--label", default="baseline")
    parser.add_argument("--compare", type=Path)
    args = parser.parse_args()
    try:
        asyncio.run(run_evaluation(Settings(), args.cases, args.output_root, args.label, args.compare))
    except (OSError, ValueError):
        parser.exit(2, "Evaluation could not start or save. Check settings, dataset, comparison hash, and that the run label is unused. Existing runs were not overwritten.\n")


if __name__ == "__main__":
    main()
