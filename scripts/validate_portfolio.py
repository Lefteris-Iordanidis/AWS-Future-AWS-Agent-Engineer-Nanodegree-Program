"""Offline integrity and secret checks for the public portfolio repository."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROMPT_PATH = ROOT / "prompts" / "prompt_design.md"
FAQ_PATH = ROOT / "knowledge" / "online_shop_faq.md"
TESTS_PATH = ROOT / "tests" / "harness-tests.json"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"FAIL: {message}")


def validate_prompt() -> None:
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    required_terms = (
        "description",
        "stepsToReproduce",
        "environment",
        "create_bug_report",
        "FAQ",
        "prompt injection",
    )
    for term in required_terms:
        require(term.lower() in prompt.lower(), f"prompt design is missing required term: {term}")


def validate_faq() -> None:
    faq = FAQ_PATH.read_text(encoding="utf-8")
    question_count = len(re.findall(r"^\d+\)", faq, flags=re.MULTILINE))
    require(question_count == 32, f"expected 32 FAQ entries, found {question_count}")


def validate_tests() -> None:
    payload = json.loads(TESTS_PATH.read_text(encoding="utf-8"))
    tests = payload.get("tests", [])
    require(len(tests) == 7, f"expected 7 tests, found {len(tests)}")

    ids = [case.get("id") for case in tests]
    require(len(set(ids)) == len(ids), "test IDs must be unique")
    require(all(case.get("prompt") and case.get("expected") for case in tests),
            "every test needs prompt and expected text")

    routes = {case.get("route") for case in tests}
    require(routes == {"bug_report", "platform_question", "other_request"},
            f"unexpected route coverage: {sorted(routes)}")


def scan_public_text() -> None:
    patterns = {
        "AWS access key": re.compile(r"(?:AKIA|ASIA)[0-9A-Z]{16}"),
        "AWS account ID": re.compile(r"(?<!\d)\d{12}(?!\d)"),
        "AWS ARN": re.compile("arn" + ":aws:", re.IGNORECASE),
        # A bare scheme in a response validator is not a bucket disclosure.
        "S3 URI": re.compile("s3" + r"://[a-z0-9][a-z0-9.-]+", re.IGNORECASE),
    }
    suffixes = {".md", ".txt", ".json", ".py", ".yml", ".yaml"}

    for path in ROOT.rglob("*"):
        if set(path.relative_to(ROOT).parts) & {".git", ".venv", "venv", "__pycache__"}:
            continue
        if not path.is_file() or path.suffix.lower() not in suffixes:
            continue
        content = path.read_text(encoding="utf-8")
        for label, pattern in patterns.items():
            require(not pattern.search(content), f"possible {label} in {path.relative_to(ROOT)}")


def validate_project3() -> None:
    project = ROOT / "projects" / "03-novamart-multi-agent-rag"
    for name in ("implementation.py", "build_runtime.py", "verify_guardrail.py"):
        compile((project / name).read_text(encoding="utf-8"), name, "exec")

    # Compare the recorded live outcomes with the five cases a reader can rerun.
    guardrail_script = ast.parse((project / "verify_guardrail.py").read_text(encoding="utf-8"))
    cases = next(node.value for node in guardrail_script.body if isinstance(node, ast.Assign)
                 and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "CASES")
    expected = [
        (label, direction, "GUARDRAIL_INTERVENED" if topic else "NONE", topic)
        for label, direction, _, topic in ast.literal_eval(cases)
    ]
    evidence = json.loads((project / "evidence" / "results.json").read_text(encoding="utf-8"))
    recorded = [(case["case"], case["direction"], case["action"], case.get("topic"))
                for case in evidence["guardrail_version_2"]]
    require(recorded == expected, "Project 3 guardrail cases and recorded outcomes differ")
    print("PASS: Project 3 Python syntax and guardrail evidence consistency")


def main() -> None:
    validate_prompt()
    validate_faq()
    validate_tests()
    scan_public_text()
    from validate_project2 import validate_project2
    validate_project2(ROOT)
    validate_project3()
    print("PASS: prompt, FAQ, tests, and public-text secret scan")


if __name__ == "__main__":
    main()

