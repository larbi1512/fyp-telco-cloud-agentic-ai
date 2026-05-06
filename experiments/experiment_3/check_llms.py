#!/usr/bin/env python3
"""
Quick connectivity check for all 5 LLMs in Experiment 3.

Pings each model with a minimal prompt and prints per-model status + latency.
Exits 0 if all pass, 2 if any fail.

Usage:
    python experiments/experiment_3/check_llms.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from experiments.experiment_3.configs import LLM_CONFIGS, LLM_ORDER
from experiments.experiment_3.run_llm_comparison import _apply_llm_config


PROMPT = 'Reply with exactly the JSON object: {"ok": true}'


def main() -> int:
    results = []
    for lid in LLM_ORDER:
        cfg = LLM_CONFIGS[lid]
        print(f"\n=== {cfg['name']} ({lid}) ===", flush=True)
        print(f"  backend={cfg['backend']}  model={cfg['model']}", flush=True)
        print(f"  endpoint={cfg['base_url']}", flush=True)

        _apply_llm_config(cfg)

        try:
            from core.llm_core import LLMCore
            llm = LLMCore()
            t0 = time.time()
            resp = llm.chat("You are a JSON-only assistant.", PROMPT)
            dt = time.time() - t0
            snippet = (resp or "").strip().replace("\n", " ")[:80]
            print(f"  [OK]   {dt:6.2f}s  →  {snippet}", flush=True)
            results.append((lid, True, dt, snippet))
        except Exception as e:
            err = f"{type(e).__name__}: {str(e)[:200]}"
            print(f"  [FAIL] {err}", flush=True)
            results.append((lid, False, 0.0, err))

    print("\n" + "=" * 60)
    print(f"{'LLM':<22} {'status':<8} {'time':>10}")
    print("-" * 60)
    for lid, ok, dt, _ in results:
        status = "OK" if ok else "FAIL"
        print(f"{LLM_CONFIGS[lid]['name']:<22} {status:<8} {dt:>8.2f}s")

    failed = [r for r in results if not r[1]]
    if failed:
        print(f"\n{len(failed)} model(s) failed:")
        for lid, _, _, err in failed:
            print(f"  - {lid}: {err}")
        return 2
    print("\nAll 5 models reachable.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
