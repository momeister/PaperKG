"""Repeatable cloud acceptance matrix. Uses the running product backend only.

No local model/embedding startup. Raw answers stay in the chosen output folder.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import time
import httpx

PAPER = "Vibrotactile Display Perception, Technology, and Applications"
QUESTIONS = [
    ("overview", "Gib einen Überblick über die wichtigsten Inhalte dieses Papers."),
    (
        "ra_pc",
        "Welche Rolle haben RA- und PC-Kanäle bei der vibrotaktilen Wahrnehmung? Erkläre räumliche und zeitliche Summation.",
    ),
    (
        "threshold",
        "Was beschreibt das Paper über absolute Wahrnehmungsschwellen, ihre Frequenzabhängigkeit und Einflussfaktoren? Nenne Zahlen und Einheiten nur bei eindeutiger Beleglage.",
    ),
    (
        "weber",
        "Was ist der Weber-Bruch und welche Wertebereiche nennt das Paper für Intensität und Frequenz?",
    ),
    (
        "recommendation",
        "Wie ordnen die Autoren die Empfehlung von 20–30 Prozent Unterschied ein? Ist das ein Primärbefund, eine Review-Zusammenfassung oder Autorenerfahrung?",
    ),
    (
        "followup",
        "Gilt das unter allen Bedingungen? Welche Einschränkungen nennen sie?",
    ),
    (
        "unanswerable",
        "Wie viele Teilnehmer hatte das kontrollierte Experiment der Autoren zu RA und PC in diesem Paper und welcher p-Wert wurde dabei gemessen?",
    ),
]


def audit(answer):
    issues = []
    if answer.get("claims_version") != 1:
        issues.append("missing_claim_contract")
    if answer.get("generation_error"):
        issues.append("generation_failed")
    if answer.get("context_diagnostics", {}).get("verification_error"):
        issues.append("verification_failed")
    evidence = {e["evidence_id"]: e for e in answer.get("evidence", [])}
    claims = {c["claim_id"]: c for c in answer.get("claims", [])}
    links = answer.get("citation_links", [])
    if any(
        c.get("verification_status") == "supported" and not c.get("evidence_ids")
        for c in claims.values()
    ):
        issues.append("supported_without_evidence")
    if len(evidence) != len(answer.get("evidence", [])):
        issues.append("duplicate_evidence_ids")
    keys = [
        (link["claim_id"], link["evidence_id"], link["citation_start"])
        for link in links
    ]
    if len(keys) != len(set(keys)):
        issues.append("duplicate_bindings")
    encoded = answer.get("answer", "").encode("utf-16-le")
    for link in links:
        claim = claims.get(link.get("claim_id"), {})
        if claim.get("verification_status") != "supported" or link.get(
            "evidence_id"
        ) not in claim.get("evidence_ids", []):
            issues.append("unsupported_binding")
        if link.get("evidence_id") not in evidence:
            issues.append("missing_evidence")
        if (
            encoded[link["citation_start"] * 2 : link["citation_end"] * 2].decode(
                "utf-16-le"
            )
            != f'[{link["paper_id"]}]'
        ):
            issues.append("bad_utf16_offset")
    return sorted(set(issues))


def run_model(model, args):
    folder = Path(args.output)
    folder.mkdir(parents=True, exist_ok=True)
    results = []
    for name, question in QUESTIONS:
        for mode in ["kg", "pdf_if_fits"]:
            # Both context modes and critical mode are represented for every
            # model; repeat the same settings to expose timing variability.
            critical = (
                QUESTIONS.index((name, question)) + (mode == "pdf_if_fits")
            ) % 2 == 1
            for repeat in range(args.repeats):
                key = f"{model.split(':')[0]}-{name}-{mode}-{repeat+1}"
                path = folder / (key + ".json")
                if path.exists() and not args.force:
                    previous = json.loads(path.read_text())
                    if not previous.get("error"):
                        # Re-audit saved answers with the current checks. Resuming
                        # must not hide failed generations behind a successful exit.
                        previous["issues"] = audit(previous.get("answer", {}))
                        results.append(previous)
                        continue
                payload = {
                    "question": question,
                    "provider": "ollama",
                    "model": model,
                    "paper_ids": [PAPER],
                    "answer_context_mode": mode,
                    "answer_style": "kritisch" if critical else "standard",
                    "llm_overrides": {"max_tokens": 8192},
                }
                if name == "followup":
                    payload["conversation_context"] = [
                        {
                            "role": "user",
                            "content": "Was empfehlen die Autoren zum Unterschied von 20–30 Prozent in Amplitude oder Frequenz?",
                        }
                    ]
                start = time.perf_counter()
                try:
                    response = httpx.post(
                        args.base + "/query/answer", json=payload, timeout=300
                    )
                    response.raise_for_status()
                    answer = response.json()
                    result = {
                        "case": key,
                        "model": model,
                        "question": question,
                        "mode": mode,
                        "critical": critical,
                        "repeat": repeat + 1,
                        "seconds": time.perf_counter() - start,
                        "timings_ms": answer.get("context_diagnostics", {}).get(
                            "timings_ms"
                        ),
                        "issues": audit(answer),
                        "supported": sum(
                            c["verification_status"] == "supported"
                            for c in answer.get("claims", [])
                        ),
                        "answer": answer,
                    }
                except Exception as exc:
                    result = {
                        "case": key,
                        "model": model,
                        "seconds": time.perf_counter() - start,
                        "error": str(exc),
                    }
                path.write_text(json.dumps(result, ensure_ascii=False, indent=2))
                results.append(result)
                print(
                    key,
                    round(result["seconds"], 1),
                    result.get("supported"),
                    result.get("issues", result.get("error")),
                    flush=True,
                )
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:42849")
    parser.add_argument("--output", default="data/eval/workspace-contract")
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--models",
        nargs="+",
        default=["deepseek-v4-flash:cloud", "glm-5.3-flash:cloud"],
        help="Explicit model IDs for every answer and helper call",
    )
    args = parser.parse_args()
    # Fail before creating misleading case files if startup has not completed.
    httpx.get(args.base + "/system/health-report", timeout=30).raise_for_status()
    with ThreadPoolExecutor(max_workers=2) as pool:
        model_results = list(
            pool.map(
                lambda model: run_model(model, args),
                args.models,
            )
        )
    results = [result for group in model_results for result in group]
    failures = [
        result for result in results if result.get("error") or result.get("issues")
    ]
    print(f"Acceptance cases: {len(results)}; failed: {len(failures)}", flush=True)
    raise SystemExit(1 if failures else 0)
