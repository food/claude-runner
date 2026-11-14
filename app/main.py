"""claude-runner: headless Claude Code CLI runs behind one HTTP endpoint."""
import asyncio
import json
import logging
import os
import time
from datetime import date

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

# A command line, so tests can put a fake CLI behind the interpreter.
CLAUDE = os.environ.get("CLAUDE_BIN", "claude").split()
RUNNER_TOKEN = os.environ.get("RUNNER_TOKEN", "")
# Sessions for --resume are stored per working directory, so it has to stay the same.
WORKDIR = os.environ.get("RUNNER_WORKDIR", os.path.expanduser("~/work"))
ROUTE = os.environ.get("RUNNER_ROUTE", "anthropic").split(",")
MAX_RUNS = int(os.environ.get("RUNNER_MAX_RUNS", "3"))
# Copilot route: the CLI talks to LiteLLM's Anthropic endpoint instead of Anthropic.
LITELLM_URL = os.environ.get("LITELLM_URL", "")
LITELLM_KEY = os.environ.get("LITELLM_KEY", "")
COPILOT_MODELS = dict(pair.strip().split("=", 1)
                      for pair in os.environ.get("COPILOT_MODELS", "").split(",") if "=" in pair)
# ISO date before which the copilot route is skipped, e.g. when the monthly quota is used up.
COPILOT_FROM = os.environ.get("COPILOT_FROM", "")

log = logging.getLogger("uvicorn.error")
app = FastAPI()
slots = asyncio.Semaphore(MAX_RUNS)
os.makedirs(WORKDIR, exist_ok=True)


class Run(BaseModel):
    prompt: str = Field(min_length=1)
    system: str | None = None  # appended to Claude Code's own system prompt
    model: str | None = None
    allowed_tools: list[str] = []
    mcp_servers: dict | None = None
    resume: str | None = None  # session_id of an earlier run
    route: list[str] | None = None  # tried in order; default RUNNER_ROUTE
    timeout: float = Field(300, gt=0, le=3600)  # per route


def command(run: Run, model: str | None) -> list[str]:
    """CLI arguments for one run; the prompt goes in on stdin."""
    cmd = [*CLAUDE, "-p", "--output-format", "json"]
    if model:
        cmd += ["--model", model]
    if run.system:
        cmd += ["--append-system-prompt", run.system]
    if run.allowed_tools:
        cmd += ["--allowedTools", ",".join(run.allowed_tools)]
    if run.mcp_servers:
        cmd += ["--mcp-config", json.dumps({"mcpServers": run.mcp_servers})]
    if run.resume:
        cmd += ["--resume", run.resume]
    return cmd


def route_setup(name: str, model: str | None) -> tuple[str | None, dict] | None:
    """Model and environment for one route, or None if the route does not apply."""
    if name == "anthropic":
        return model, dict(os.environ)
    if name == "copilot":
        if not LITELLM_URL or model not in COPILOT_MODELS or date.today().isoformat() < COPILOT_FROM:
            return None
        env = {k: v for k, v in os.environ.items()
               if k not in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")}
        env.update(ANTHROPIC_BASE_URL=LITELLM_URL, ANTHROPIC_AUTH_TOKEN=LITELLM_KEY or "-",
                   CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1")
        # The CLI does small side tasks with Haiku; those must not go to Anthropic either.
        if "claude-haiku-4-5" in COPILOT_MODELS:
            env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] = COPILOT_MODELS["claude-haiku-4-5"]
        return COPILOT_MODELS[model], env
    raise HTTPException(400, f"unknown route {name!r}, use anthropic or copilot")


async def execute(cmd: list[str], env: dict, prompt: str, timeout: float) -> dict:
    """One CLI run. Exit code, is_error and an empty result all count as failure;
    the CLI writes errors like "OAuth session expired" to stdout, so both streams go into the message."""
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=WORKDIR, env=env, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(prompt.encode()), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"timeout after {timeout:.0f} s")
    try:
        data = json.loads(out)
    except ValueError:
        data = None
    if proc.returncode or not isinstance(data, dict) or data.get("is_error") \
            or not str(data.get("result") or "").strip():
        tail = (err + out).decode(errors="replace").strip()[-500:]
        raise RuntimeError(f"exit {proc.returncode}: {tail}")
    return data


@app.post("/v1/run")
async def run_claude(run: Run, authorization: str | None = Header(None)):
    """Run the prompt on the first route that answers; 503 with every route's error otherwise."""
    if RUNNER_TOKEN and authorization != f"Bearer {RUNNER_TOKEN}":
        raise HTTPException(401, "bearer token required")
    errors = []
    for name in run.route or ROUTE:
        setup = route_setup(name, run.model)
        if setup is None:
            errors.append(f"{name}: not available for model {run.model}")
            continue
        model, env = setup
        start = time.monotonic()
        try:
            async with slots:
                data = await execute(command(run, model), env, run.prompt, run.timeout)
        except Exception as e:
            errors.append(f"{name}: {e}")
            log.warning("run %s %s failed after %.0f s: %s", name, model, time.monotonic() - start, e)
            continue
        if name == "copilot":
            data["total_cost_usd"] = None  # the CLI prices with Anthropic's list, not the subscription
        log.info("run %s %s %.0f s", name, model, time.monotonic() - start)
        return {**data, "route": name, "model": model, "failed_routes": errors}
    raise HTTPException(503, "; ".join(errors))


_version = None


@app.get("/health")
async def health():
    """Liveness and the installed CLI version."""
    global _version
    if _version is None:
        proc = await asyncio.create_subprocess_exec(*CLAUDE, "--version",
                                                    stdout=asyncio.subprocess.PIPE)
        _version = (await proc.communicate())[0].decode().strip()
    return {"ok": True, "cli": _version, "route": ROUTE}
