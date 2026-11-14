"""Stand-in for the claude CLI in test_runner.py.

Answers with the recorded replies in fixtures/ and appends what it was called with
(arguments, stdin, the route-relevant environment) to the file in FAKE_LOG.
The prompt steers it: "expired" answers like a lapsed login, "slow" sleeps,
"copilot-down" fails whenever the copilot route is active."""
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
if sys.argv[1:] == ["--version"]:
    print("2.1.325 (Claude Code)")
    sys.exit(0)

prompt = sys.stdin.read()
copilot = "ANTHROPIC_BASE_URL" in os.environ
with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as f:
    f.write(json.dumps({"argv": sys.argv[1:], "stdin": prompt, "cwd": os.getcwd(),
                        "base_url": os.environ.get("ANTHROPIC_BASE_URL"),
                        "oauth": "CLAUDE_CODE_OAUTH_TOKEN" in os.environ,
                        "own": [k for k in ("RUNNER_TOKEN", "LITELLM_KEY") if k in os.environ],
                        "haiku": os.environ.get("ANTHROPIC_DEFAULT_HAIKU_MODEL")}) + "\n")

if "slow" in prompt:
    time.sleep(30)
if "expired" in prompt or ("copilot-down" in prompt and copilot):
    print((HERE / "fixtures" / "expired.json").read_text(encoding="utf-8"))
    sys.exit(1)
reply = json.loads((HERE / "fixtures" / "ok.json").read_text(encoding="utf-8"))
reply["result"] = f"answer to: {prompt}"
print(json.dumps(reply))
