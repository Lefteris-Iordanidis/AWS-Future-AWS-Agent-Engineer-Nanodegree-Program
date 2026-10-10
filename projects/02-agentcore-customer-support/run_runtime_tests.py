"""Record real cloud Runtime evidence; run from the project virtual environment.

Example: python -u run_runtime_tests.py --scenario refund
Uses the existing deployment. No infrastructure creation, deployment, or model
invocation retries. The separate successful agentcore invoke log proves CLI use.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import time
import uuid
from decimal import Decimal, InvalidOperation


PROMPTS = {
    "order": "Please track my order ORD-001. Tell me its status, carrier, tracking number and estimated delivery date.",
    "refund": "Please refund my delivered order ORD-002 because it arrived damaged. Check my order and the applicable return policy, then initiate the refund for its full price. I authorize this demo refund. Include the returned refund ID in your answer.",
    "rag": "Is the Kindle Paperwhite waterproof? Check the product catalog and cite your source.",
    "discount": "I am Gold with 4250 loyalty points. Calculate the maximum allowed points redemption and tier discount on a $150 order consisting entirely of accessories, using the standard earn rate. Use the Code Interpreter and show the final total and projected points balance.",
    "browser": "Use your live browser to open https://example.org and read the text from the page body. Tell me the heading and what the page is for, with the URL. Do not answer from memory.",
}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def stamp():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    temporary.replace(path)


def save_record(path, record):
    write_json(path, record)
    Path(path).with_suffix(".txt").write_text(
        "REAL AWS AGENTCORE RUNTIME TEST\n" + json.dumps(record, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )


def parse_response(raw, content_type):
    """Accept the entrypoint's JSON object; reject missing or ambiguous results."""
    def decode(value):
        for _ in range(2):
            if not isinstance(value, str):
                break
            value = json.loads(value)
        return value

    try:
        value = decode(raw)
        if isinstance(value, dict):
            return value
    except (ValueError, TypeError):
        pass
    if "text/event-stream" in content_type:
        values = []
        for event in re.split(r"\r?\n\r?\n", raw):
            data = "\n".join(line[5:].lstrip() for line in event.splitlines() if line.startswith("data:"))
            if not data or data == "[DONE]":
                continue
            try:
                value = decode(data)
            except (ValueError, TypeError):
                continue
            if isinstance(value, dict) and "status" in value and "response" in value:
                values.append(value)
        if len(values) == 1:
            return values[0]
    raise ValueError("Expected one complete JSON response from main.invoke; raw response was saved")


def invoke_record(client, config, payload, session_id, scenario, logs_dir):
    path = Path(logs_dir) / f"runtime_{scenario}_{stamp()}.json"
    record = {
        "test_type": "cloud_agentcore_runtime_invocation", "scenario": scenario,
        "started_at": utc_now(), "agent_arn": config["agent_arn"],
        "qualifier": "DEFAULT", "region": config["region"],
        "runtime_session_id": session_id, "request": payload,
        "status": "started", "automatic_invocation_retries": 0,
    }
    save_record(path, record)
    print(f"CALLING_RUNTIME: {scenario}\nSESSION: {session_id}\nSAVED: {path}", flush=True)
    try:
        response = client.invoke_agent_runtime(
            agentRuntimeArn=config["agent_arn"], runtimeSessionId=session_id,
            qualifier="DEFAULT", contentType="application/json", accept="application/json",
            payload=json.dumps(payload).encode("utf-8"),
        )
        record["aws_request_id"] = response.get("ResponseMetadata", {}).get("RequestId")
        record["http_status"] = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        record["content_type"] = response.get("contentType", "application/json")
        stream = response["response"]
        try:
            raw = stream.read()
        finally:
            stream.close()
        record["raw_response"] = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
        record["status"] = "received"
        save_record(path, record)  # Keep raw evidence even if parsing or checks fail.
        record["result"] = parse_response(record["raw_response"], record["content_type"])
    except Exception as exc:
        record["status"] = "error"
        record["error"] = {"type": type(exc).__name__, "message": str(exc)}
        print(f"INVOCATION_ERROR: {type(exc).__name__}: {exc}", flush=True)
    finally:
        # A completed test does not need a warm Runtime. Memory extraction is
        # managed by the separate Memory service and continues after this stop.
        try:
            stopped = client.stop_runtime_session(
                agentRuntimeArn=config["agent_arn"], runtimeSessionId=session_id, qualifier="DEFAULT",
            )
            record["cleanup"] = {"status": "stop_requested", "response": stopped}
        except Exception as exc:
            record["cleanup"] = {"status": "warning", "message": str(exc)}
            print("SESSION_STOP_WARNING:", str(exc), flush=True)
        record["finished_at"] = utc_now()
        save_record(path, record)
    result = record.get("result", {})
    if isinstance(result, dict):
        print("RESPONSE:", result.get("response", "(no agent response)"), flush=True)
        diagnostics = result.get("diagnostics")
        if isinstance(diagnostics, dict) and isinstance(diagnostics.get("tools"), list):
            for item in diagnostics["tools"]:
                if isinstance(item, dict):
                    print("TOOL:", item.get("name"), "STATUS:", item.get("status"), flush=True)
    return record, path


def common_checks(result, session_id):
    if not isinstance(result, dict):
        return [{"check": "response_is_json_object", "passed": False}]
    return [
        {"check": "agent_completed", "passed": result.get("status") == "ok"
         and result.get("stop_reason") in ("end_turn", "stop_sequence")
         and isinstance(result.get("response"), str) and bool(result["response"].strip())},
        {"check": "runtime_session_matches", "passed": result.get("session_id") == session_id},
    ]


def report_checks(checks):
    for check in checks:
        print(("PASSED: " if check["passed"] else "REVIEW_REQUIRED: ") + check["check"], flush=True)
    return bool(checks) and all(check["passed"] for check in checks)


def recheck_saved_log(path):
    """Review saved evidence without importing boto3 or changing the source log."""
    path = Path(path).resolve()
    source = path.read_bytes()
    record = json.loads(source)
    scenario = record.get("scenario")
    if record.get("test_type") != "cloud_agentcore_runtime_invocation" or scenario not in PROMPTS:
        raise ValueError("Provide an original order/refund/rag/discount/browser Runtime JSON log")
    session_id = record.get("runtime_session_id")
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("Original log has no Runtime session ID")
    checks = validate_runtime_result(record.get("result"), scenario, expected_session_id=session_id)
    checks.append({"check": "original_invocation_has_no_recorded_error", "passed": not record.get("error")})
    print("OFFLINE_RECHECK:", scenario, flush=True)
    print("AWS_API_CALLS: 0", flush=True)
    passed = report_checks(checks)
    review = {
        "test_type": "offline_review_of_saved_runtime_evidence", "reviewed_at": utc_now(),
        "validator_revision": "2026-10-08-grounding-review-v3",
        "source_log": str(path), "source_sha256": hashlib.sha256(source).hexdigest(),
        "scenario": scenario, "runtime_session_id": session_id,
        "checks": checks, "status": "passed" if passed else "review_required", "aws_api_calls": 0,
        "note": "Original evidence is unchanged. Checks verify listed criteria, not every natural-language claim.",
    }
    output = path.with_name(path.stem + "_review_" + stamp() + ".json")
    write_json(output, review)
    output.with_suffix(".txt").write_text(
        "OFFLINE REVIEW OF SAVED RUNTIME EVIDENCE\n" + json.dumps(review, indent=2) + "\n", encoding="utf-8")
    print("SAVED:", output, flush=True)
    print(f"RUNTIME_{scenario.upper()}_RECHECK_OK" if passed else "REVIEW_REQUIRED: share the original JSON log.", flush=True)
    return 0 if passed else 2


def run():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--scenario", choices=[*PROMPTS, "memory"])
    mode.add_argument("--recheck", type=Path, help="Review an existing Runtime JSON log; makes zero AWS calls")
    parser.add_argument("--memory-wait-seconds", type=int, default=180)
    args = parser.parse_args()
    if not 0 <= args.memory_wait_seconds <= 600:
        parser.error("--memory-wait-seconds must be 0 to 600")
    if args.recheck:
        return recheck_saved_log(args.recheck)
    root = Path(__file__).resolve().parent
    config = json.loads((root / "runtime_zip_config.json").read_text(encoding="utf-8"))
    if not config.get("agent_arn") or not config.get("region"):
        raise RuntimeError("runtime_zip_config.json needs agent_arn and region")

    import boto3
    from botocore.config import Config

    session = boto3.Session(region_name=config["region"])
    identity = session.client("sts").get_caller_identity()
    arn_parts = config["agent_arn"].split(":")
    if len(arn_parts) != 6 or arn_parts[4] != identity["Account"] or arn_parts[3] != config["region"]:
        raise RuntimeError("AWS account/region does not match the deployed Runtime")
    # One request per test: never silently retry a potentially completed refund.
    client = session.client("bedrock-agentcore", config=Config(
        connect_timeout=10, read_timeout=300,
        retries={"mode": "standard", "total_max_attempts": 1},
    ))
    if args.scenario == "memory":
        return run_memory(client, config, root, wait_seconds=args.memory_wait_seconds)
    session_id = str(uuid.uuid4())
    payload = {"prompt": PROMPTS[args.scenario], "customer_id": "CUST-123", "debug": True}
    record, path = invoke_record(client, config, payload, session_id, args.scenario, root / "test_logs")
    checks = validate_runtime_result(record.get("result"), args.scenario, expected_session_id=session_id)
    record["checks"] = checks
    passed = report_checks(checks)
    record["status"] = "passed" if passed else "review_required"
    save_record(path, record)
    print("SAVED:", path, flush=True)
    print("SAVED:", path.with_suffix(".txt"), flush=True)
    if passed:
        print(f"RUNTIME_{args.scenario.upper()}_TEST_OK", flush=True)
        return 0
    print("REVIEW_REQUIRED: share this output and the JSON log before repeating the invocation.", flush=True)
    return 2


def record_text(record):
    return "\n".join(str(block.get("text", ""))
                     for block in record.get("content", []) if isinstance(block, dict))


def record_objects(record):
    """Decode both Gateway API results and Lambda proxy response bodies."""
    values = []
    for block in record.get("content", []):
        if not isinstance(block, dict):
            continue
        value = block.get("json", block.get("text"))
        for _ in range(4):
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except (ValueError, TypeError):
                    break
            elif isinstance(value, dict) and "statusCode" in value:
                try:
                    if int(value["statusCode"]) != 200:
                        break
                except (ValueError, TypeError):
                    break
                value = value.get("body")
            else:
                break
        if isinstance(value, dict) and "statusCode" not in value and "error" not in value:
            values.append(value)
    return values


def unsupported_label_claims(answer, label_results):
    """Flag affirmative label statements without a returned label URL.

    This is a conservative English demo check, not general claim verification.
    A review flag never triggers another refund automatically.
    """
    urls = [item.get("label_url") for item in label_results
            if isinstance(item.get("label_url"), str)
            and item["label_url"].startswith("https://")]
    if any(url in answer for url in urls):
        return []
    text = answer.casefold().replace("\u2019", "'").replace("*", "")
    label = r"\b(?:(?:prepaid|return|shipping)\s+)*labels?\b"
    action = (r"(?:generat(?:e|ed)|creat(?:e|ed)|issu(?:e|ed)|provid(?:e|ed)|"
              r"available|ready|access|download|print|view)")
    claims = []
    for clause in re.split(r"[.!?;\n]+", text):
        # Explicit negatives must not be mistaken for generated-label claims.
        candidate = re.sub(r"\bno\s+" + label, "NEGATED_LABEL", clause)
        candidate = re.sub(
            r"\b(?:not|never|cannot|can't|didn't|hasn't|haven't|isn't|wasn't)\s+"
            r"(?:(?:yet|been|be|already)\s+){0,2}" + action + r"\b",
            "NEGATED_ACTION", candidate)
        if (re.search(label + r".{0,100}\b" + action + r"\b", candidate)
                or re.search(r"\b" + action + r"\b.{0,100}" + label, candidate)):
            claims.append(clause.strip())
    return claims


def unsupported_waterproof_measurements(answer, passages):
    """Review numeric depth/time additions absent from the returned KB text.

    Scoped to the English Kindle waterproof test. This does not establish
    entailment, validate unit conversions, or check every possible factual claim.
    """
    measure = re.compile(
        r"\b(\d+(?:\.\d+)?)\s*(inches|inch|feet|foot|ft|meters?|metres?|cm|mm|m|"
        r"minutes?|mins?|seconds?|secs?|hours?|hrs?)\b", re.IGNORECASE)
    aliases = {
        "inches": "inch", "feet": "foot", "ft": "foot", "meters": "meter",
        "metre": "meter", "metres": "meter", "m": "meter", "minutes": "minute",
        "min": "minute", "mins": "minute", "seconds": "second", "sec": "second",
        "secs": "second", "hours": "hour", "hr": "hour", "hrs": "hour",
    }

    def key(match):
        unit = match.group(2).casefold()
        return Decimal(match.group(1)), aliases.get(unit, unit)

    supported = {key(match) for match in measure.finditer("\n".join(passages))}
    water_context = re.compile(r"waterproof|water.resistan|ipx8|immersi|submer|underwater|depth", re.I)
    previous = ""
    unsupported = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", answer.replace("*", "")):
        if water_context.search(sentence) or water_context.search(previous):
            unsupported.extend(match.group(0) for match in measure.finditer(sentence)
                               if key(match) not in supported)
        previous = sentence
    return list(dict.fromkeys(unsupported))


def validate_runtime_result(result, scenario, *, expected_session_id,
                            customer_id="CUST-123"):
    """Return evidence checks; missing/malformed tool data cannot pass.

    Scenarios assume the five prompts in agent_implementation/run_local.py.
    Cross-session memory has its own two-session checker in the recorder.
    """
    checks = []

    def check(name, condition):
        checks.append({"check": name, "passed": bool(condition)})

    if not isinstance(result, dict):
        check("response_is_json_object", False)
        return checks
    check("agent_completed", result.get("status") == "ok"
          and result.get("stop_reason") in ("end_turn", "stop_sequence")
          and isinstance(result.get("response"), str) and bool(result["response"].strip()))
    check("runtime_session_matches", result.get("session_id") == expected_session_id)
    diagnostics = result.get("diagnostics")
    tools = diagnostics.get("tools", []) if isinstance(diagnostics, dict) else []
    check("debug_tool_trace_present", isinstance(tools, list) and bool(tools))
    if not isinstance(tools, list):
        return checks
    successful = [r for r in tools if isinstance(r, dict)
                  and r.get("status") == "success" and not r.get("exception_type")]
    answer = str(result.get("response", ""))

    def bodies(name):
        return [body for record in successful if record.get("name") == name
                for body in record_objects(record)]

    if scenario == "order":
        orders = bodies("OrdersApi___get_order")
        valid = [o for o in orders if o.get("order_id") == "ORD-001"
                 and o.get("customer_id") == customer_id and o.get("status") == "SHIPPED"
                 and o.get("carrier") == "UPS" and o.get("tracking_number") == "TRK987654321"
                 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(o.get("estimated_delivery", "")))]
        check("api_target_returned_verified_order", valid)
        check("answer_includes_order_status_and_carrier", "ORD-001" in answer
              and "shipped" in answer.lower() and "UPS" in answer)

    elif scenario == "refund":
        orders = bodies("OrdersApi___get_order")
        check("api_target_verified_refund_order", any(
            o.get("order_id") == "ORD-002" and o.get("customer_id") == customer_id
            and o.get("status") == "DELIVERED" and money_equals(o.get("total"), "139.99")
            for o in orders))
        check("policy_was_retrieved", any(
            r.get("name") == "search_knowledge_base" and "Source: s3://" in record_text(r)
            and "Return" in record_text(r) for r in successful))
        refunds = bodies("RefundLambda___initiate_refund")
        good = [r for r in refunds if r.get("order_id") == "ORD-002"
                and r.get("status") == "APPROVED" and money_equals(r.get("amount"), "139.99")
                and re.fullmatch(r"REF-[A-Z0-9]{8}", str(r.get("refund_id", "")))]
        check("lambda_target_returned_approved_refund", good)
        check("answer_includes_actual_refund_id", any(r["refund_id"] in answer for r in good))
        check("verified_refund_receipt_used", isinstance(diagnostics, dict)
              and diagnostics.get("response_mode") == "verified_refund_receipt")
        claims = unsupported_label_claims(answer, bodies("RefundLambda___get_return_label"))
        check("no_unsupported_return_label_claim", not claims)
        if claims:
            checks[-1]["review_details"] = claims

    elif scenario == "rag":
        hits = [record_text(r) for r in successful if r.get("name") == "search_knowledge_base"]
        check("retrieval_contains_product_and_source", any(
            "Source: s3://" in t and "product_catalog.txt" in t
            and "Kindle Paperwhite" in t and "IPX8" in t for t in hits))
        check("answer_grounded_in_waterproof_rating", "IPX8" in answer
              and "waterproof" in answer.lower())
        # Source URLs may be shortened to the file name by the model.
        check("answer_cites_catalog", "product_catalog" in answer
              or "s3://" in answer or bool(re.search(r"\[\d+\]", answer)))
        measurements = unsupported_waterproof_measurements(answer, hits)
        check("waterproof_depth_and_time_numbers_supported", not measurements)
        if measurements:
            checks[-1]["review_details"] = measurements

    elif scenario == "discount":
        quotes = bodies("calculate_loyalty_discount")
        check("sandbox_executed_without_fallback", any(
            q.get("execution_mode") == "code_interpreter" and q.get("fallback_used") is False
            for q in quotes))
        check("discount_quote_matches_business_rules", any(
            q.get("execution_mode") == "code_interpreter" and q.get("fallback_used") is False
            and q.get("points_redeemed") == 4000 and q.get("tier_discount_pct") == 10
            and money_equals(q.get("final_total"), "99.00")
            and q.get("remaining_points") == 349 and q.get("points_earned") == 99
            and q.get("points_after_redemption") == 250
            and q.get("is_accessory_order") is True and q.get("earn_rate") == 1
            for q in quotes))

    elif scenario == "browser":
        browser_calls = [r for r in successful if r.get("name") == "browser"]
        # example.org can expose its purpose as body text without an H1 title.
        # The observed content, successful tool call and requested URL are all
        # required; accepting a model answer alone would not prove live browsing.
        def normalized(text):
            return " ".join(re.sub(r"\\[nrt]", " ", text).casefold().split())

        check("live_browser_returned_page_content", any(
            any(anchor in normalized(record_text(r)) for anchor in (
                "example domain", "for use in documentation examples")) for r in browser_calls))
        check("browser_requested_expected_public_url", any(
            "https://example.org" in json.dumps(r.get("input", {})) for r in browser_calls))
        answer_normalized = normalized(answer)
        check("answer_identifies_page_and_url", "example.org" in answer_normalized
              and any(anchor in answer_normalized for anchor in ("example domain", "documentation examples")))
    else:
        raise ValueError(f"Unknown scenario: {scenario}")
    return checks


def money_equals(value, expected):
    try:
        return Decimal(str(value)) == Decimal(expected)
    except (InvalidOperation, TypeError, ValueError):
        return False


def run_memory(client, config, root, wait_seconds=180):
    """Prove cross-session recall; retries resume extraction without reseeding."""
    import json
    import re
    import time
    import uuid
    from bedrock_agentcore.memory import MemoryClient

    logs = root / "test_logs"
    logs.mkdir(exist_ok=True)
    state_path = logs / "runtime_memory_state.json"
    memory_id = json.loads((root / "memory_config.json").read_text())["memory_id"]
    recall_prompt = (
        "What name and headphone preferences do you remember about me from our previous "
        "conversation? Include preferred colour and anything I avoid. Use remembered "
        "information only; do not look up a customer profile or invent details."
    )

    def facts_present(text):
        text = text.casefold()
        return ("eleni" in text and "purple" in text
                and ("earbud" in text or "in-ear" in text or "in ear" in text))

    def answer_preferences_match(text):
        text = text.casefold().replace("\u2019", "'")
        avoids = re.search(
            r"\b(?:avoid(?:s|ing)?|dislikes?|no|(?:do not|don't|does not|doesn't|not)"
            r"\s+(?:like|want|prefer|use))\b[^.!?;\n]{0,100}\b(?:in[- ]ear|earbuds?)\b", text)
        return bool(avoids and re.search(r"\bover[- ]ear\b", text)
                    and re.search(r"\b(?:noise[- ]cancell?(?:ing|ation)|anc)\b", text))

    def save_state(stage=None):
        if stage:
            state["stage"] = stage
        state["updated_at"] = utc_now()
        write_json(state_path, state)

    def finish_record(record, path, session_id, phase):
        result = record.get("result")
        checks = common_checks(result, session_id)
        result = result if isinstance(result, dict) else {}
        diagnostics = result.get("diagnostics") or {}
        memory = diagnostics.get("memory") or {}
        checks.append({"check": "memory_event_saved", "passed": (
            memory.get("memory_save_status") == "saved" and bool(memory.get("event_id")))})
        if phase == "recall":
            recalled = memory.get("recalled_texts") or []
            checks.extend([
                {"check": "distinct_runtime_sessions", "passed": state["seed_session"] != session_id},
                {"check": "fresh_session_history", "passed": (
                    diagnostics.get("history_messages") == 0 and memory.get("history_turns") == 0)},
                {"check": "retrieved_seed_facts", "passed": (
                    memory.get("recalled_count", 0) > 0 and facts_present("\n".join(recalled)))},
                {"check": "strategy_tagged_memories", "passed": (
                    bool(recalled) and all(re.match(r"^\[[A-Z_]+\] ", text) for text in recalled))},
                {"check": "all_strategy_namespaces_queried", "passed": (
                    {(item.get("strategy_type"), item.get("namespace"))
                     for item in memory.get("memory_retrievals", []) if item.get("status") == "ok"}
                    == {(item["strategy_type"], item["namespace"]) for item in state["namespaces"]})},
                {"check": "answer_recalls_seed_facts", "passed": facts_present(result.get("response", ""))},
                {"check": "answer_preserves_preference_meaning", "passed":
                    answer_preferences_match(result.get("response", ""))},
            ])
        record["checks"] = checks
        passed = report_checks(checks) and not record.get("error")
        record["status"] = "passed" if passed else "review_required"
        save_record(path, record)
        return passed

    if state_path.exists():
        state = json.loads(state_path.read_text())
        if any(state.get(key) != value for key, value in {
            "agent_arn": config["agent_arn"], "region": config["region"], "memory_id": memory_id,
        }.items()):
            raise RuntimeError("Memory test state belongs to different AWS resources; do not overwrite it.")
    else:
        state = {
            "agent_arn": config["agent_arn"], "region": config["region"], "memory_id": memory_id,
            "actor_id": "CUST-MEM-" + uuid.uuid4().hex,
            "seed_session": str(uuid.uuid4()), "recall_session": str(uuid.uuid4()),
            "stage": "new", "created_at": utc_now(), "polls": [],
        }
        save_state()
    if state["stage"] == "complete":
        print("MEMORY_TEST_ALREADY_PASSED:", state.get("recall_log"), flush=True)
        return 0
    if state["stage"] in {"seed_started", "recall_started", "seed_failed", "recall_failed"}:
        print("MEMORY_TEST_REVIEW_REQUIRED:", state["stage"], flush=True)
        print("Existing invocation will not be replayed. Share the saved state and test logs.")
        return 1
    if state["stage"] not in {"new", "seeded_waiting"}:
        raise RuntimeError("Unrecognized memory test stage; preserve the state for review.")
    if state["stage"] == "new":
        payload = {
            "customer_id": state["actor_id"], "debug": True,
            "prompt": "My name is Eleni. I prefer purple over-ear noise-cancelling headphones, "
                      "and I avoid in-ear earbuds. Please remember my name and these preferences "
                      "for future conversations.",
        }
        state["seed_payload"] = payload
        state["recall_prompt"] = recall_prompt
        save_state("seed_started")
        record, path = invoke_record(client, config, payload, state["seed_session"], "memory_seed", logs)
        state["seed_log"] = str(path)
        if not finish_record(record, path, state["seed_session"], "seed"):
            save_state("seed_failed")
            return 1
        save_state("seeded_waiting")

    memory_client = MemoryClient(region_name=config["region"])
    namespaces = []
    for strategy in memory_client.get_memory_strategies(memory_id=memory_id):
        kind = strategy.get("type") or strategy.get("memoryStrategyType") or "UNKNOWN"
        strategy_id = strategy.get("strategyId") or strategy.get("memoryStrategyId") or ""
        for template in strategy.get("namespaceTemplates") or strategy.get("namespaces") or []:
            if "{actorId}" not in template:
                raise RuntimeError("Memory namespace lacks customer scoping.")
            namespace = template.format(actorId=state["actor_id"], sessionId=state["recall_session"],
                                        memoryId=memory_id, memoryStrategyId=strategy_id, strategyId=strategy_id)
            if (kind, namespace) not in namespaces:
                namespaces.append((kind, namespace))
    if not namespaces:
        raise RuntimeError("No memory strategy namespaces are configured.")
    state["namespaces"] = [{"strategy_type": kind, "namespace": ns} for kind, ns in namespaces]
    deadline = time.monotonic() + max(0, min(wait_seconds, 600))
    while True:
        poll = {"timestamp": utc_now(), "retrievals": []}
        texts = []
        try:
            for kind, namespace in namespaces:
                response = client.retrieve_memory_records(
                    memoryId=memory_id, namespace=namespace,
                    searchCriteria={"searchQuery": state["recall_prompt"], "topK": 3})
                rows = [{"memory_record_id": row.get("memoryRecordId"),
                         "text": row.get("content", {}).get("text", "")}
                        for row in response.get("memoryRecordSummaries", [])]
                poll["retrievals"].append({"strategy_type": kind, "namespace": namespace, "records": rows})
                texts.extend(row["text"] for row in rows if isinstance(row["text"], str))
        except Exception as exc:
            poll["error"] = str(exc)
            state["polls"].append(poll)
            save_state()
            print("MEMORY_RETRIEVAL_FAILED:", exc, flush=True)
            return 1
        poll["seed_facts_ready"] = facts_present("\n".join(texts))
        state["polls"].append(poll)
        save_state()
        print("MEMORY_EXTRACTION_READY:", poll["seed_facts_ready"], flush=True)
        if poll["seed_facts_ready"]:
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            print("MEMORY_EXTRACTION_PENDING: rerun this scenario to resume; seed will not repeat.")
            print("SAVED:", state_path, flush=True)
            return 2
        time.sleep(min(15, remaining))
    payload = {"customer_id": state["actor_id"], "debug": True, "prompt": state["recall_prompt"]}
    save_state("recall_started")
    record, path = invoke_record(client, config, payload, state["recall_session"], "memory_recall", logs)
    state["recall_log"] = str(path)
    passed = finish_record(record, path, state["recall_session"], "recall")
    save_state("complete" if passed else "recall_failed")
    print("MEMORY_TEST_PASSED" if passed else "MEMORY_TEST_FAILED", flush=True)
    print("SAVED:", state_path, flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    try:
        sys.exit(run())
    except KeyboardInterrupt:
        print("INTERRUPTED: saved logs/state are preserved; no invocation was retried.")
        sys.exit(130)
    except Exception as exc:
        print(f"TEST_ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
