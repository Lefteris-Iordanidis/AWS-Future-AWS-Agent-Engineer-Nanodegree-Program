"""Run from the starter project root: python verify_guardrail.py (five live Bedrock checks)."""

from pathlib import Path

import boto3
from dotenv import dotenv_values


CASES = (
    ("Price calculation", "INPUT", "How much are 5 items at $29.99 with 10% off?", None),
    ("Price calculation answer", "OUTPUT", "The total with the specified discount is $134.96.", None),
    ("New price request", "INPUT", "If I order today, will NovaMart lower the advertised price for me?", "pricing negotiations"),
    ("Competitor products", "INPUT", "Compare NovaMart's headphones with Best Buy's competing products.", "competitor products"),
    ("Legal threat", "INPUT", "I will take NovaMart to court unless you approve my refund.", "legal threats"),
)


def main() -> int:
    env = dotenv_values(Path(__file__).resolve().with_name(".env"))
    identifier = env.get("GUARDRAIL_ID", "")
    version = env.get("GUARDRAIL_VERSION", "")
    if not identifier or not version or not version.isdigit():
        raise SystemExit("Set GUARDRAIL_ID and the numbered GUARDRAIL_VERSION in the project .env.")
    client = boto3.client("bedrock-runtime", region_name=env.get("AWS_REGION") or "us-east-1")
    failures = 0
    for label, source, text, expected_topic in CASES:
        result = client.apply_guardrail(
            guardrailIdentifier=identifier,
            guardrailVersion=version,
            source=source,
            content=[{"text": {"text": text}}],
        )
        action = result["action"]
        blocked_topics = {
            topic["name"].casefold()
            for assessment in result.get("assessments", [])
            for topic in assessment.get("topicPolicy", {}).get("topics", [])
            if topic.get("action") == "BLOCKED"
        }
        if expected_topic:
            ok = action == "GUARDRAIL_INTERVENED" and expected_topic in blocked_topics
        else:
            ok = action == "NONE"
        details = ", ".join(sorted(blocked_topics)) or "none"
        print(f"{'PASS' if ok else 'FAIL'}  {source:6s}  {label}: {action}; denied topics: {details}")
        failures += int(not ok)
    return int(failures > 0)


if __name__ == "__main__":
    raise SystemExit(main())
