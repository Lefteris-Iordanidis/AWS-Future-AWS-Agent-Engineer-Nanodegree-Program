"""Install the NovaMart implementation into a local copy of the pinned Udacity starter."""

import argparse
import ast
import hashlib
import os
from pathlib import Path
import shutil
import tempfile


STARTER_SHA256 = "c3eb0b30796fd0bc87bebf771f6c1d8e304f7203e51ff4e66d93a29534fd98be"
COMPLETED_SHA256 = "e25ba5f2ec3ff7cea2f1b46ce545cbda9eb67e6ac35ef58c314071819ee00d03"

# Changes to existing caller functions, expressed as zero-based line slices within
# each function. The upstream revision is pinned so it supplies its own CLI and
# HTTP handlers. Only the lines contributed by this project are stored here.
CALLER_EDITS = {
    "_read_workflow_state": [
        (5, 6, "    response = table.get_item(Key={'session_id': session_id}, ConsistentRead=True)"),
    ],
    "run_test_scenarios": [
        (4, 5, ""),
        (7, 7, "        orchestrator = build_agent_graph()"),
        (11, 12, ""),
        (13, 14, "            response = _respond_to_customer(orchestrator, session_id, customer_id, query)"),
    ],
    "run_chat": [
        (59, 61, ""),
        (68, 69, "                text = _respond_to_customer(orchestrator, session_id, customer_id, user_input)"),
        (73, 78, ""),
    ],
    "run_serve": [
        (13, 16, """    Each request gets its own agent graph and conversation history. Shared
    workflow data lives in DynamoDB. The supplied tracer records one active
    request per process, while policy retrievals run concurrently inside it."""),
        (23, 33, """    app = BedrockAgentCoreApp()
    request_lock = threading.Lock()
    setup_logging()"""),
        (44, 51, """        with request_lock:
            with tracer.trace_request(session_id, customer_id, prompt):
                text = _respond_to_customer(build_agent_graph(), session_id, customer_id, prompt)
            trace_id = tracer.last_trace_id
            flush_logs()"""),
        (52, 53, "                'trace_id': trace_id}"),
    ],
}


def assemble(starter: str, implementation: str) -> str:
    source = implementation.splitlines()
    functions = {
        node.name: source[node.lineno - 1:node.end_lineno]
        for node in ast.parse(implementation).body if isinstance(node, ast.FunctionDef)
    }
    functions["build_inventory_agent"] = (
        functions.pop("_check_scope") + ["", ""] + functions["build_inventory_agent"]
    )
    functions["build_orchestrator_agent"] += ["", ""] + functions.pop("_respond_to_customer")

    lines = starter.splitlines()
    for node in reversed(ast.parse(starter).body):
        if isinstance(node, ast.ImportFrom) and node.module == "strands.models":
            lines.insert(node.end_lineno, "from strands.tools.executors import SequentialToolExecutor")
        if not isinstance(node, ast.FunctionDef):
            continue
        start = node.lineno - 1
        if node.name in functions:
            lines[start:node.end_lineno] = functions[node.name]
        for first, last, replacement in reversed(CALLER_EDITS.get(node.name, [])):
            lines[start + first:start + last] = replacement.splitlines()
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("starter", type=Path, help="Path to the classroom project/starter directory")
    path = parser.parse_args().starter / "src" / "agent_orchestrator.py"
    starter = path.read_text(encoding="utf-8")
    digest = hashlib.sha256(starter.encode()).hexdigest()
    if digest == COMPLETED_SHA256:
        print("The reviewed NovaMart implementation is already installed.")
        return
    if digest != STARTER_SHA256:
        parser.exit(1, "This file differs from the pinned starter. Use a fresh copy at the revision in the README.\n")
    implementation = Path(__file__).with_name("implementation.py").read_text(encoding="utf-8")
    result = assemble(starter, implementation)
    compile(result, str(path), "exec")
    with tempfile.TemporaryDirectory(dir=path.parent) as staging:
        temporary = Path(staging) / path.name
        temporary.write_text(result, encoding="utf-8")
        shutil.copymode(path, temporary)
        os.replace(temporary, path)
    print(f"Installed NovaMart in {path}")


if __name__ == "__main__":
    main()
