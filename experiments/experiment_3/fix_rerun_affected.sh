#!/bin/bash
# Remove rows for LLMs with broken type scoring and re-run them.
# Run this AFTER the main experiment finishes.
set -e
cd /home/larbi/fyp

RESULTS=experiments/experiment_3/results
AFFECTED="llama32_3b qwen25_7b deepseek_r1_7b"

echo "=== Backing up store ==="
cp "$RESULTS/runs.csv"   "$RESULTS/runs.csv.bak"
cp "$RESULTS/raw.jsonl"  "$RESULTS/raw.jsonl.bak"

echo "=== Removing affected rows ==="
python3 - <<'PYEOF'
import json, csv, sys
from pathlib import Path

affected = {"llama32_3b", "qwen25_7b", "deepseek_r1_7b"}
results = Path("experiments/experiment_3/results")

# Filter runs.csv
with open(results / "runs.csv") as f:
    rows = list(csv.DictReader(f))
header = list(rows[0].keys()) if rows else []
kept = [r for r in rows if (r.get("llm_id") or r.get("system")) not in affected]
removed_csv = len(rows) - len(kept)

with open(results / "runs.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=header)
    w.writeheader()
    w.writerows(kept)

# Filter raw.jsonl
kept_j, removed_j = [], 0
with open(results / "raw.jsonl") as f:
    for line in f:
        line = line.strip()
        if not line: continue
        r = json.loads(line)
        if (r.get("llm_id") or r.get("system")) in affected:
            removed_j += 1
        else:
            kept_j.append(line)

with open(results / "raw.jsonl", "w") as f:
    f.write("\n".join(kept_j) + ("\n" if kept_j else ""))

print(f"Removed {removed_csv} rows from runs.csv, {removed_j} from raw.jsonl")
print(f"Remaining: {len(kept)} in runs.csv, {len(kept_j)} in raw.jsonl")
PYEOF

echo "=== Re-running affected LLMs ==="
source venv/bin/activate
nohup python3 experiments/experiment_3/run_llm_comparison.py \
    --llms llama32_3b qwen25_7b deepseek_r1_7b \
    --reps 5 --intents full \
    >> "$RESULTS/rerun.log" 2>&1 &
echo "Re-run PID: $!"
echo $! > "$RESULTS/rerun.pid"
echo "Tailing rerun.log (Ctrl+C to detach):"
sleep 2
tail -f "$RESULTS/rerun.log"
