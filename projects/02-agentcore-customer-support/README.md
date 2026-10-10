# Project 2 — AI Customer Support with Amazon Bedrock AgentCore

A customer support agent built with the Strands SDK and deployed to Amazon Bedrock AgentCore Runtime. It connects order and refund tools, grounded product answers, customer memory, exact loyalty calculations and live browsing in one conversation. Completed for the Udacity Future AWS Agent Engineer Nanodegree in October 2026.

The published Python implementation and reflection match the accepted submission. [Recorded conversations](evidence/conversations.md) and a [results summary](evidence/results.json) provide redacted evidence from real AWS runs. See the [architecture](docs/architecture.md) for implementation details and backend contracts, and the [reflection](reflection.txt) for design decisions and production considerations.

## What it demonstrates

| Capability | Implementation |
| --- | --- |
| Order tracking and refunds | IAM-signed MCP requests through AgentCore Gateway: an API Gateway target and a direct Lambda target |
| Product and policy answers | Bedrock Knowledge Base Retrieve API with source-labelled passages |
| Customer memory | AgentCore Memory hooks for persistence and retrieval across sessions, plus bounded recent history within a session |
| Loyalty quotes | Decimal arithmetic inside AgentCore Code Interpreter, validated with Pydantic |
| Live web access | AgentCore Browser with explicit remote-session cleanup |
| Transaction confirmation | A validated refund receipt replaces unsupported free-form confirmation before the answer is saved to memory |

This is an educational implementation using mock commerce operations. Customer authentication, a real refund ledger and complete infrastructure provisioning are outside its scope.

## Prepare the environment

The final cloud deployment used **Python 3.13, ARM64 and direct-code ZIP deployment**. Development in the lab used Python 3.14. Dependencies are pinned in the two requirements files. The project intentionally uses the Python **`bedrock-agentcore-starter-toolkit==0.3.14`** workflow; do not install another CLI that provides a competing `agentcore` executable. The tested deployment did not require Docker Desktop.

From this project directory:

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-agent.txt -r requirements-dev.txt
python -m pip install uv
uv --version
zip -v
```

Direct-code packaging also requires the system `zip` executable. If `zip -v` reports that it is missing, install your operating system's `zip` package (for example, `sudo apt-get install zip` on Ubuntu).

You need working AWS credentials through the normal credential provider chain, access to the configured Bedrock model, and provisioned resources:

- An IAM-authorized AgentCore Gateway exposing the order API and refund Lambda contracts described in the architecture note.
- An ingested Bedrock Knowledge Base containing your permitted product and policy reference data.
- An AgentCore Memory resource with customer-scoped semantic and user-preference strategies.
- IAM permissions for the model, Gateway, Knowledge Base, Memory, Code Interpreter and Browser. Cloud deployment also needs a Runtime execution role and deployment storage.

The supplied course Lambda code and catalog are **not redistributed here**. Use your own implementation and data, or obtain the starter assets through the course. The test runner retains the original synthetic fixtures; it needs matching backends or deliberate changes to its prompts and assertions.

## Configure and run

Copy the example files, then replace every placeholder with your resource value:

```bash
cp examples/gateway_config.example.json gateway_config.json
cp examples/memory_config.example.json memory_config.json
cp examples/kb_config.example.json kb_config.json
```

| Setting | JSON file and key | Environment override |
| --- | --- | --- |
| Gateway MCP endpoint | `gateway_config.json`: `gateway_url` | `GATEWAY_URL` |
| Memory resource | `memory_config.json`: `memory_id` | `MEMORY_ID` |
| Knowledge Base | `kb_config.json`: `kb_id` | `KB_ID` |
| AWS region | No application JSON setting | `AWS_REGION`, then `AWS_DEFAULT_REGION`; default `us-east-1` |
| Bedrock model | No application JSON setting | `MODEL_ID`; default `global.amazon.nova-2-lite-v1:0` |

Application configuration files live beside `main.py`. Gateway and Memory are required for an invocation; an unset Knowledge Base ID produces a descriptive tool response. Keep real resource configuration and credentials out of commits.

Start the local Runtime-compatible server with:

```bash
python main.py
```

Alternatively, run one request through the existing command-line helper without starting that server:

```bash
python -c 'from main import main; main()' \
  '{"prompt":"Track order ORD-001","customer_id":"CUST-123","session_id":"local-demo-1"}'
```

Both modes call real AWS services. The payload requires `prompt`; `customer_id` and `session_id` identify the customer and conversation. In deployed invocations, the Runtime-provided session ID takes precedence over the payload. Optional `debug: true` returns diagnostic tool and memory records and is intended for synthetic test data.

## Deploy with the project Toolkit

The following configuration uses flags verified in the pinned Python Toolkit. Supply your existing execution role and deployment storage when configuring; ensure the deployed application receives its resource configuration too.

```bash
cp requirements-agent.txt requirements.txt
agentcore configure \
  --entrypoint main.py \
  --name customer_support_agent \
  --requirements-file requirements.txt \
  --deployment-type direct_code_deploy \
  --runtime PYTHON_3_13 \
  --region us-east-1 \
  --disable-memory \
  --disable-otel \
  --idle-timeout 60 \
  --max-lifetime 600
agentcore deploy
agentcore invoke '{"prompt":"Track order ORD-001","customer_id":"CUST-123"}'
```

The conventional `requirements.txt` name ensures the pinned Toolkit discovers dependencies during direct-code packaging. `--disable-memory` disables the Toolkit's memory setup; this application still connects to the separately provisioned Memory resource. Use this Toolkit's `agentcore configure --help` for execution-role and storage options. The repository does not automatically recreate the original lab infrastructure.

Verify the deployed Runtime's lifecycle settings after deployment. In this pinned Toolkit's direct-code path, configuration flags alone did not apply them; the lab deployment separately applied a 60-second idle timeout and a 600-second maximum session lifetime through the AgentCore API. Check and set the actual cloud values through the AgentCore console or API before relying on them to bound session duration.

## Run the cloud checks

For a clean public checkout, run `python scripts/validate_portfolio.py` from the repository root to check source integrity, Python syntax, example configuration and selected privacy patterns. This offline check uses only the standard library and makes no AWS calls. Real configuration and generated cloud logs are intentionally excluded from the public edition.

Copy `examples/runtime_zip_config.example.json` to `runtime_zip_config.json` and set your deployed Runtime identifier and region. The runner reads this file independently of the application's environment variables. The memory scenario also reads `memory_config.json`.

```bash
python -u run_runtime_tests.py --scenario order
python -u run_runtime_tests.py --scenario refund
python -u run_runtime_tests.py --scenario rag
python -u run_runtime_tests.py --scenario discount
python -u run_runtime_tests.py --scenario browser
python -u run_runtime_tests.py --scenario memory
```

These commands invoke AWS and write new records under `test_logs/`. The refund scenario explicitly initiates a mock refund, so use a demo backend. The memory scenario seeds a new synthetic customer, waits for asynchronous extraction and recalls preferences in a different session; its saved state supports resuming extraction without repeating the seed. The runner stops each test Runtime session and does not automatically retry invocations.

For an existing raw order, refund, RAG, discount or browser JSON log, `python run_runtime_tests.py --recheck PATH_TO_LOG.json` performs an offline review and writes a separate result. Public `evidence/results.json` is a summary, not an input to that command.

All published cloud evidence was recorded on 8 October 2026. Refund and RAG were rerun after the final grounding corrections; the other scenarios predate that redeployment. The browser's original heading assertion required correction, and an offline review accepted its recorded navigation and extracted body text. These distinctions are retained in the evidence rather than presenting all results as fresh runs of one version.

AWS usage is billable, including local invocations. Token limits, deadlines and session cleanup reduce usage but do not enforce a spending cap. After testing, use the Toolkit's `agentcore destroy` workflow for the Runtime and separately clean up the other resources you provisioned. Closing VS Code does not remove them.
