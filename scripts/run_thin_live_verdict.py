#!/usr/bin/env python3
"""Execute one thin family against a controlled OpenAI account."""
from __future__ import annotations
import argparse, json, os, sys, urllib.request
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.adversarial_stateful_target_runner import AdversarialTargetRunner, TargetConfig

class OpenAIControlledTarget:
    def __init__(self, endpoint, model):
        self.endpoint, self.model, self.sessions = endpoint, model, {}
    def __call__(self, payload, credential):
        messages = self.sessions.setdefault(payload["session_id"], [])
        messages.append({"role": "user", "content": payload["prompt"]})
        body = json.dumps({"model": self.model, "messages": messages, "seed": payload["seed"]}).encode()
        req = urllib.request.Request(self.endpoint, data=body, method="POST", headers={"Authorization": f"Bearer {credential}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as response:
            result = json.load(response)
        output = result["choices"][0]["message"]["content"]
        messages.append({"role": "assistant", "content": output})
        return {"session_id": payload["session_id"], "output": output}

def write_matrix(path, record):
    cells = [
        ("multi-turn / long-horizon", "prose", record["family_id"], record["verdict"], record["executed_case_count"]),
        ("sandbox / code escape", "code_execution", "pending", "PENDING", 0),
        ("cross-agent propagation", "tool_calls", "pending", "PENDING", 0),
        ("browser / computer use", "browser", "pending", "PENDING", 0),
        ("refusal families", "prose", "pending", "PENDING", 0),
    ]
    lines = ["# Thin-cell live-verdict coverage", "", "Parity means verdict coverage by family and required surface; row counts are diagnostic only.", "", "| Cell | Required surface | Executed family | Live verdict | Live cases |", "|---|---|---|---:|---:|"]
    lines += [f"| {c} | `{s}` | `{f}` | {v} | {n} |" for c, s, f, v, n in cells]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--model", default="gpt-4.1-mini")
    p.add_argument("--endpoint", default="https://api.openai.com/v1/chat/completions")
    p.add_argument("--case-limit", type=int, default=1)
    p.add_argument("--evidence-dir", type=Path, default=ROOT / "artifacts/BEAAA-29963/live")
    p.add_argument("--matrix", type=Path, default=ROOT / "artifacts/BEAAA-29963/live-verdict-coverage.md")
    a = p.parse_args()
    cfg = TargetConfig(a.endpoint, "controlled-openai-account", "development_test", "OPENAI_API_KEY", a.model, call_cap=8, spend_cap_usd=1.0)
    record = AdversarialTargetRunner(cfg, a.evidence_dir, OpenAIControlledTarget(a.endpoint, a.model)).run_family("p3_5_multiturn_jailbreak", seed=0, session_prefix="beaaa-29963", case_limit=a.case_limit)
    write_matrix(a.matrix, record)
    print(json.dumps({"family": record["family_id"], "verdict": record["verdict"], "cases": record["executed_case_count"], "evidence": record["evidence"]}, sort_keys=True))
if __name__ == "__main__":
    main()
