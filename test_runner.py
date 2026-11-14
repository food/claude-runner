"""Self-check against fake_claude.py, which answers with recorded CLI replies.
python test_runner.py"""
import json
import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

here = Path(__file__).parent
tmp = Path(tempfile.mkdtemp())
calls = tmp / "calls.jsonl"
# A Claude Code session running this test exports these itself.
for var in ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_DEFAULT_HAIKU_MODEL"):
    os.environ.pop(var, None)
os.environ.update(
    CLAUDE_BIN=f"{sys.executable} {here / 'fake_claude.py'}", FAKE_LOG=str(calls),
    RUNNER_WORKDIR=str(tmp / "work"), RUNNER_TOKEN="secret", RUNNER_ROUTE="anthropic",
    CLAUDE_CODE_OAUTH_TOKEN="oauth", LITELLM_URL="http://litellm:4000", LITELLM_KEY="sk-x",
    COPILOT_MODELS="claude-opus-5-5=copilot-claude-opus,claude-haiku-4-5=copilot-claude-haiku")

from app import main  # noqa: E402  (reads the environment on import)

api = TestClient(main.app)
auth = {"Authorization": "Bearer secret"}


def run(**body):
    """POST /v1/run with the token."""
    return api.post("/v1/run", json=body, headers=auth)


def last_call():
    """What the fake CLI saw on its latest start."""
    return json.loads(calls.read_text(encoding="utf-8").splitlines()[-1])


# --- default route: every option becomes its CLI flag, the prompt goes in on stdin
r = run(prompt="Wie warm ist es?", system="Antworte kurz.", model="claude-haiku-4-5",
        allowed_tools=["WebSearch", "mcp__memory"], resume="abc",
        mcp_servers={"memory": {"type": "http", "url": "http://m/mcp"}})
assert r.status_code == 200, r.text
body = r.json()
assert body["result"] == "answer to: Wie warm ist es?" and body["route"] == "anthropic", body
assert body["session_id"] and body["usage"]["output_tokens"] == 41 and body["total_cost_usd"] > 0
seen = last_call()
argv = seen["argv"]
assert argv[:3] == ["-p", "--output-format", "json"], argv
assert argv[argv.index("--model") + 1] == "claude-haiku-4-5"
assert argv[argv.index("--append-system-prompt") + 1] == "Antworte kurz."
assert argv[argv.index("--allowedTools") + 1] == "WebSearch,mcp__memory"
assert json.loads(argv[argv.index("--mcp-config") + 1]) == {
    "mcpServers": {"memory": {"type": "http", "url": "http://m/mcp"}}}
assert argv[argv.index("--resume") + 1] == "abc" and seen["stdin"] == "Wie warm ist es?"
assert seen["oauth"] and seen["base_url"] is None, seen
assert Path(seen["cwd"]).resolve() == (tmp / "work").resolve(), (seen["cwd"], tmp)

# --- copilot first: mapped model, LiteLLM instead of Anthropic, no made-up cost
r = run(prompt="x", model="claude-opus-5-5", route=["copilot", "anthropic"])
body, seen = r.json(), last_call()
assert body["route"] == "copilot" and body["model"] == "copilot-claude-opus", body
assert body["total_cost_usd"] is None and body["failed_routes"] == []
assert seen["base_url"] == "http://litellm:4000" and not seen["oauth"]
assert seen["haiku"] == "copilot-claude-haiku"

# --- copilot fails: Anthropic answers, the copilot error is reported along
r = run(prompt="copilot-down", model="claude-opus-5-5", route=["copilot", "anthropic"])
body = r.json()
assert body["route"] == "anthropic" and body["model"] == "claude-opus-5-5", body
failed = body["failed_routes"][0]
assert failed["route"] == "copilot" and failed["model"] == "copilot-claude-opus", failed
assert "OAuth session expired" in failed["error"] and not failed["skipped"], failed

# --- copilot does not apply: model without mapping, or before COPILOT_FROM
r = run(prompt="x", model="claude-sonnet-5", route=["copilot", "anthropic"])
assert r.json()["route"] == "anthropic" and r.json()["failed_routes"][0]["skipped"]
main.COPILOT_FROM = "2999-01-01"
assert run(prompt="x", model="claude-opus-5-5", route=["copilot", "anthropic"]).json()["route"] == "anthropic"
main.COPILOT_FROM = ""

# --- every route fails: 503 with the CLI's own message
r = run(prompt="expired")
detail = r.json()["detail"]
assert r.status_code == 503 and "OAuth session expired" in detail["error"], r.text
assert detail["failed_routes"][0]["route"] == "anthropic", detail

# --- a hanging CLI is killed after the timeout
r = run(prompt="slow", timeout=2)
assert r.status_code == 503 and "timeout after 2 s" in r.json()["detail"]["error"], r.text

# --- input checks
assert api.post("/v1/run", json={"prompt": "x"}).status_code == 401
assert run(prompt="x", route=["openai"]).status_code == 400
assert run(prompt="").status_code == 422
assert api.get("/health").json()["cli"] == "2.1.325 (Claude Code)"

print("ok")
