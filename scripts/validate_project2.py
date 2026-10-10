"""Offline publication checks. Does not import the agent or contact AWS."""

from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path


SOURCE_HASHES = {
    "main.py": "e79c27bab53dba13ce8bb9259580b57ce4b1d536098653e35521cc4d345374cc",
    "run_runtime_tests.py": "afa1da03c9d0af52fb388a76e74a1775acc0d412f612e663f1e8ad583cafc964",
}


def validate_project2(root: Path) -> None:
    project = root / "projects" / "02-agentcore-customer-support"

    def require(condition: bool, message: str) -> None:
        if not condition:
            raise SystemExit(f"FAIL: Project 2: {message}")

    for name, expected in SOURCE_HASHES.items():
        path = project / name
        require(path.is_file(), f"missing {name}")
        source = path.read_bytes()
        require(hashlib.sha256(source).hexdigest() == expected,
                f"{name} differs from the reviewed cloud-tested source; review and update the baseline deliberately")
        ast.parse(source.decode("utf-8"), filename=name)

    config_fields = {
        "gateway_config": {"gateway_url"},
        "memory_config": {"memory_id"},
        "kb_config": {"kb_id"},
        "runtime_zip_config": {"agent_arn", "region"},
    }
    for name, keys in config_fields.items():
        path = project / "examples" / f"{name}.example.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        require(set(payload) == keys, f"unexpected fields in {path.name}")
        for key, value in payload.items():
            require(value == "us-east-1" if key == "region" else
                    isinstance(value, str) and value.startswith("<your-") and value.endswith(">"),
                    f"example {name}.{key} must not contain real configuration")

    for required in ("README.md", "docs/architecture.md", "evidence/conversations.md",
                     "evidence/results.json", "reflection.txt", "requirements-agent.txt",
                     "requirements-dev.txt"):
        require((project / required).is_file(), f"missing {required}")
    json.loads((project / "evidence/results.json").read_text(encoding="utf-8"))

    forbidden_names = {"gateway_config.json", "memory_config.json", "kb_config.json",
                       "runtime_zip_config.json", "deployment_record.json", "submission_manifest.json"}
    patterns = {
        "temporary AWS access key": re.compile(r"ASIA[0-9A-Z]{16}"),
        "private key": re.compile("-----BEGIN " + r"(?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
        "lab path": re.compile("/voc/" + "work"),
        "concrete AWS endpoint": re.compile(r"https://[^\s\"<>]+\.(?:execute-api|gateway\.bedrock-agentcore)\.[^\s\"<>]+"),
    }
    for path in project.rglob("*"):
        if set(path.relative_to(project).parts) & {".git", ".venv", "venv", "__pycache__"}:
            continue
        require(not path.is_symlink(), f"unexpected symlink: {path.name}")
        if not path.is_file():
            continue
        require(path.name not in forbidden_names and "test_logs" not in path.relative_to(project).parts,
                f"private generated file present: {path.relative_to(project)}")
        if path.suffix in {".py", ".json", ".md", ".txt"}:
            text = path.read_text(encoding="utf-8")
            for label, pattern in patterns.items():
                require(not pattern.search(text), f"possible {label} in {path.relative_to(project)}")
    print("PASS: Project 2 source hashes, Python syntax, placeholder configuration, evidence files and publication scan")


if __name__ == "__main__":
    validate_project2(Path(__file__).resolve().parents[1])
