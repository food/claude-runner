# claude-runner

Runs the Claude Code CLI headless (`claude -p`) behind one HTTP endpoint, so
services share one login and one CLI version instead of installing their own.
A run can go through GitHub Copilot via LiteLLM first and fall back to
Anthropic.

## Request

```
POST /v1/run
Authorization: Bearer <RUNNER_TOKEN>
{"prompt": "Wie warm wird es morgen?", "system": "Antworte in zwei Saetzen.",
 "model": "claude-haiku-4-5", "allowed_tools": ["WebSearch", "mcp__memory"],
 "mcp_servers": {"memory": {"type": "http", "url": "http://memory:8765/mcp"}},
 "resume": null, "route": ["copilot", "anthropic"], "timeout": 300}
```

| Field | Meaning |
|---|---|
| `prompt` | Sent on stdin. |
| `system` | `--append-system-prompt`, added to Claude Code's own. |
| `model` | `--model`. |
| `allowed_tools` | `--allowedTools`; without it the CLI may use only its permission-free tools. |
| `mcp_servers` | Passed as `--mcp-config`. |
| `resume` | `session_id` of an earlier run to continue it. |
| `route` | Tried in order: `anthropic` (the login) and `copilot` (LiteLLM, only for models in `COPILOT_MODELS`). Default `RUNNER_ROUTE`. |
| `timeout` | Seconds per route, up to 3600. |

The answer is the CLI's own JSON (`result`, `session_id`, `usage`,
`total_cost_usd`, ...) plus `route`, the `model` actually asked, `seconds` and
`failed_routes` (each `{route, model, error, seconds, skipped}`; `skipped`
means the route does not serve that model). On the copilot route `total_cost_usd` is null: the CLI prices
with Anthropic's list, which says nothing about a Copilot subscription. A
route counts as failed on a non-zero exit, `is_error`, an empty result or the
timeout; if all fail the answer is 503 with `detail: {error, failed_routes}`.

At most `RUNNER_MAX_RUNS` CLI processes run at once, the rest wait.

## Run

```
cp .env.example .env   # token from `claude setup-token`, RUNNER_TOKEN, LiteLLM
docker compose up -d --build
```

`./home` keeps the CLI's sessions, so `resume` survives a rebuild.

## Test

```
python test_runner.py
```

`fake_claude.py` stands in for the CLI and answers with replies recorded from
the real one (`fixtures/`).
