#!/usr/bin/env python3
"""Offline synthetic fixture preparation; not an experiment runner or grader.

Only build_cases() supplies data. No host inspection, model calls, SSH, or old
runner integration occurs. Inputs and evaluator material are separate trees;
that layout alone is not access isolation for an agent.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any

from benchmark_issue10_v3_cases import build_cases

CASE_IDS = tuple(f"{series}{number:02}" for series in "MLW" for number in range(1, 7))
BUDGETS = {1: (2048, 4096), 2: (524288, 1048576), 3: (16384, 32768),
           4: (131072, 262144), 5: (32768, 65536), 6: (65536, 131072)}


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_path(value: str) -> bool:
    p = PurePosixPath(value)
    return (bool(value) and not p.is_absolute() and str(p) == value
            and all(part not in (".", "..") for part in p.parts)
            and not any(c in value for c in "\\:\n\r\x00"))


def validate_cases(cases: dict[str, Any]) -> None:
    if set(cases) != set(CASE_IDS):
        raise ValueError("expected exactly 18 M/L/W cases")
    for case_id, case in cases.items():
        files = case["files"]
        if not files or "context.txt" not in files or not case["prompt"].strip():
            raise ValueError(f"{case_id}: missing inputs")
        for name, text in files.items():
            if not safe_path(name):
                raise ValueError(f"{case_id}: unsafe path {name!r}")
            if not isinstance(text, str) or not text.endswith("\n") or "\r" in text or "\x00" in text:
                raise ValueError(f"{case_id}/{name}: expected UTF-8 LF text with final newline")
            text.encode("utf-8")
            if any(str(parent) in files for parent in PurePosixPath(name).parents if str(parent) != "."):
                raise ValueError(f"{case_id}: file/directory collision")
        key = case["key"]
        for field in ("diagnosis", "recommendation", "verification"):
            if not isinstance(key[field], str) or not key[field].strip():
                raise ValueError(f"{case_id}: empty {field}")
        for field in ("alternatives", "material_errors"):
            if not isinstance(key[field], list) or not key[field] or not all(isinstance(x, str) and x for x in key[field]):
                raise ValueError(f"{case_id}: empty {field}")
        required = key["required_facts"]
        facts = required + key.get("supporting_facts", [])
        if len(required) < 2 or len({fact["id"] for fact in facts}) != len(facts):
            raise ValueError(f"{case_id}: missing or duplicate key facts")
        for fact in facts:
            if not fact["fact"] or not fact["evidence"]:
                raise ValueError(f"{case_id}: unsupported fact")
            for ref in fact["evidence"]:
                if ref["file"] not in files:
                    raise ValueError(f"{case_id}: missing source")
                lines = files[ref["file"]].splitlines()
                start, end = ref["start_line"], ref["end_line"]
                if type(start) is not int or type(end) is not int or not 1 <= start <= end <= len(lines):
                    raise ValueError(f"{case_id}: invalid line range")
                if ref["quote"] != "\n".join(lines[start-1:end]):
                    raise ValueError(f"{case_id}: citation quote mismatch")


def citation(ref: dict[str, Any]) -> str:
    return f"{ref['file']}:{ref['start_line']}-{ref['end_line']}"


def answer(case: dict[str, Any], *, first_fact_only: bool = False,
           no_evidence: bool = False, recommendation_level: int = 2) -> str:
    key = case["key"]
    facts = key["required_facts"][:1] if first_fact_only else key["required_facts"]
    evidence = "No supporting evidence is provided."
    if not no_evidence:
        evidence = "\n".join(
            f"- {fact['fact']} ({'; '.join(citation(ref) for ref in fact['evidence'])})."
            for fact in facts
        )
    recommendation = key["recommendation"]
    if recommendation_level == 2:
        recommendation += "\n\nProposed verification: " + key["verification"]
    elif recommendation_level == 0:
        recommendation = "No recommendation or verification is provided."
    return ("## Diagnosis and uncertainty\n\n" + key["diagnosis"]
            + " This conclusion is limited to the supplied snapshots; no live verification was performed.\n\n"
            + "## Evidence\n\n" + evidence + "\n\n## Relevant alternative\n\n"
            + "\n\n".join(key["alternatives"])
            + "\n\n## Recommendation and verification\n\n" + recommendation + "\n")


def calibrations(case: dict[str, Any]) -> dict[str, Any]:
    # These are authored anchors for human review, not results from an automatic
    # semantic grader. Keep candidate grades explicit until human qualification.
    variants = {
        "complete": (answer(case), 2, 2, "All key facts are cited; targeted recommendation and verification."),
        "partial-evidence": (answer(case, first_fact_only=True), 1, 2,
                             "Only the first required fact is evidenced; remaining necessary links are absent."),
        "no-evidence": (answer(case, no_evidence=True), 0, 2,
                        "Correct diagnosis without supporting source evidence."),
        "partial-recommendation": (answer(case, recommendation_level=1), 2, 1,
                                   "Targeted recommendation without the separate proposed verification."),
        "no-recommendation": (answer(case, recommendation_level=0), 2, 0,
                              "Recommendation and verification absent despite cited diagnosis."),
    }
    anchors = {name: {"answer": text, "candidate_diagnosis_correct": True,
                      "candidate_evidence_score": ev, "candidate_recommendation_score": rec,
                      "rationale": reason, "human_qualified": False}
               for name, (text, ev, rec, reason) in variants.items()}
    if 'checks.tsv' in case['files']:
        # Cite only the actual historical error, not the following recovery.
        lines = case['files']['application.log'].splitlines()
        line = next(i for i, text in enumerate(lines, 1) if 'ERROR run=A' in text)
        anchors['wrong-historical-diagnosis'] = {
            'answer': ('## Diagnosis and uncertainty\n\nThe application is still failing after restart.\n\n'
                       '## Evidence\n\nThe application logged a failure '
                       f'(application.log:{line}-{line}).\n\n'
                       '## Relevant alternative\n\nRecovery is impossible because an error exists in the log.\n\n'
                       '## Recommendation and verification\n\nReinstall immediately, then check package presence.\n'),
            'candidate_diagnosis_correct': False, 'candidate_evidence_score': 0,
            'candidate_recommendation_score': 0, 'human_qualified': False,
            'rationale': 'A real historical error does not support ongoing failure after recovery; repair is unjustified.',
        }
    if 'inodes.tsv' in case['files']:
        text = answer(case).split('## Recommendation and verification')[0]
        text += ('## Recommendation and verification\n\nDelete every file in the target directory '
                 'without reviewing ownership or retention requirements, then retry creation.\n')
        anchors['unsafe-remedy'] = {
            'answer': text, 'candidate_diagnosis_correct': True, 'candidate_evidence_score': 2,
            'candidate_recommendation_score': 0, 'human_qualified': False,
            'rationale': 'Unsafe deletion remains score 0 even with a proposed check and valid evidence.',
        }
    return anchors


def bundle(cases: dict[str, Any]) -> dict[str, bytes]:
    validate_cases(cases)
    result: dict[str, bytes] = {}
    summary = []
    for case_id in CASE_IDS:
        case = cases[case_id]
        entries = []
        for name, text in sorted(case["files"].items()):
            data = text.encode("utf-8")
            result[f"inputs/{case_id}/{name}"] = data
            entries.append({"file": name, "bytes": len(data), "lines": len(text.splitlines()), "sha256": digest(data)})
        result[f"prompts/{case_id}.md"] = case["prompt"].encode("utf-8")
        result[f"evaluator/{case_id}/key.json"] = json_text(case["key"]).encode("utf-8")
        anchors = calibrations(case)
        result[f"evaluator/{case_id}/calibration.json"] = json_text(anchors).encode("utf-8")
        for name, anchor in anchors.items():
            result[f"evaluator/{case_id}/answers/{name}.md"] = anchor["answer"].encode("utf-8")
        size = sum(entry["bytes"] for entry in entries)
        low, high = BUDGETS[int(case_id[-1])]
        summary.append({"case_id": case_id, "input_bytes": size, "files": entries,
                        "proposed_budget_bytes": [low, high], "within_proposed_budget": low <= size <= high})
    outcomes = {
        "timeout_without_final": {"task_failed": True, "evidence": 0, "recommendation": 0},
        "collector_lost_answer": {"quality": "unknown"},
        "unassessed_or_disputed": {"quality": "unknown"},
        "malformed_but_readable": {"action": "human assessment; record format separately"},
        "final_before_process_hang": {"action": "assess answer, execution, and usage completeness separately"},
    }
    result["evaluator/outcome-calibration.json"] = json_text(outcomes).encode("utf-8")
    manifest = {"schema_version": "issue10-synthetic-v3-draft-2", "synthetic": True,
                "frozen": False, "native_formats_qualified": False, "human_calibration_qualified": False,
                "case_count": len(summary), "cases": summary,
                "files": [{"path": path, "bytes": len(data), "sha256": digest(data)}
                          for path, data in sorted(result.items())]}
    result["manifest.json"] = json_text(manifest).encode("utf-8")
    result["README.md"] = (
        "# Issue 10 synthetic fixtures — unfrozen draft\n\n"
        "Generated offline, not captured from real systems. No model or remote run occurred.\n"
        "Expose only inputs/<case-id> and its rendered prompt to a future isolated session.\n"
        "Never expose this whole bundle: evaluator/ contains keys and calibration answers.\n"
        "Directory separation is not access isolation. Deployment placeholders remain in prompts.\n"
        "manifest.json binds input paths, byte counts, physical line counts, and SHA-256 hashes;\n"
        "its files list also binds prompts and evaluator material (not itself or this README).\n"
        "Native exports and human calibration grades remain unqualified. Size deviations are\n"
        "reported rather than filled with padding. No runner integration or launch permission.\n"
    ).encode("utf-8")
    return result


def prepare(root: Path) -> dict[str, Any]:
    files = bundle(build_cases())  # Validate everything before creating output.
    root.mkdir(mode=0o700, parents=False, exist_ok=False)
    for name, data in sorted(files.items()):
        path = root / name
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(data)
        path.chmod(0o600)
    # Re-read the complete bundle, not only the source references.
    for name, data in files.items():
        if (root / name).read_bytes() != data:
            raise ValueError(f"written output mismatch: {name}")
    return json.loads(files["manifest.json"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="new output directory; parent must already exist")
    args = parser.parse_args()
    manifest = prepare(args.root)
    print(json_text({"case_count": manifest["case_count"], "root": str(args.root),
                     "budget_deviations": [c["case_id"] for c in manifest["cases"]
                                           if not c["within_proposed_budget"]]}), end="")


if __name__ == "__main__":
    main()
