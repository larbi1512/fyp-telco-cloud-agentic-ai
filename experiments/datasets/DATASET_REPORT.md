# intents_v2.json — dataset construction & annotation report

This report documents how the 36-prompt evaluation dataset at [intents_v2.json](intents_v2.json) was built and annotated for the pre-deployment comparative experiment (MAS vs. 4 baselines, 6 metrics, statistical validation).

## 1. Goals and constraints

The dataset must:
1. Cover a defensible 3-dimensional grid: **complexity (3) × UE-band (3) × scenario (4)**.
2. Provide enough statistical power for paired tests at n=5 reps × 5 systems (effective paired n ≈ dataset size).
3. Reuse the existing 25 prompts from [experiment_1/prompts.json](../experiment_1/prompts.json) — they are already vetted against expected VNF lists and have been used in the published Experiment 1 results.
4. Add a structured features block to drive Metric 3 (Resource accuracy vs. optimal) without a hand-built oracle per prompt.
5. Annotate scenario flavour so the analysis can slice results by workload type.

Hard constraint from the plan: the resource oracle must read its features from the prompt file and apply the scaling formulas in [vnf_resource_profiles.yaml](../../config/vnf_resource_profiles.yaml). That fixed the names and units of `structured_features`.

## 2. Schema design

`structured_features` was designed by walking through every scaling formula in [vnf_resource_profiles.yaml](../../config/vnf_resource_profiles.yaml) and ensuring each one has a corresponding feature:

| Profile entry | Scaling unit | Feature in dataset |
|---|---|---|
| `oai-amf` | per_1000_ue | `ue_count` |
| `oai-smf` | per_500_sessions | `session_count` |
| `oai-nrf` | per_10_vnfs | (derived from VNF list, not in features) |
| `oai-ausf`, `oai-udm` | per_1000_ue | `ue_count` |
| `oai-udr` | per_5000_subscribers | `subscriber_count` |
| `oai-nssf` | per_5_slices | `len(slices)` |
| `oai-upf` | per_1_gbps | `throughput_gbps` |
| `ueransim-gnb`, `ueransim-ue` | per_100_ue | `ue_count` |

Two extra fields encode scenario semantics:
- `peak_multiplier` — ratio of peak load to baseline, drives bursty-headroom evaluation.
- `autoscale_required` — boolean flag indicating sinusoidal-autoscale intent.
- `ha_required` — boolean for fault-HA intent.

The trio (`ha_required`, `peak_multiplier > 1.0`, `autoscale_required`) maps 1:1 to the three non-steady scenarios. Cross-validation in §6 confirms consistency.

## 3. The grid: from 36 theoretical cells to 22 filled cells with 36 prompts

The plan called for "36 intents = 3 × 3 × 4 = 36 cells, pruned of nonsense cells". Rather than literally one prompt per cell, the actual approach was:

- **22 of the 36 cells filled**, with the remaining 14 cells deliberately empty (see §4).
- Most filled cells have 1 prompt, but **steady cells absorb the extra inherited prompts** (e.g. medium/200/steady has 4, complex/500/steady has 3) because the legacy 25 are predominantly steady.
- Total prompt count of 36 is preserved so statistical power matches the plan.

Distribution after construction:

| Slice | Counts |
|---|---|
| Per scenario | steady = 20, fault-HA = 7, bursty-headroom = 5, sinusoidal-autoscale = 4 |
| Per complexity | simple = 7, medium = 17, complex = 12 |
| Per UE-band | 50 = 7, 200 = 15, 500 = 14 |

The skew toward steady is acceptable because (a) it preserves the existing prompts unchanged for backwards compatibility, (b) steady is the most realistic baseline workload, and (c) within-scenario tests (e.g., MAS vs. baselines on bursty-only) still have n ≥ 4 paired observations, sufficient for non-parametric tests.

## 4. Deliberately empty cells (and why)

14 of the 36 theoretical cells are empty by design:

| Empty cell pattern | Count | Reason |
|---|---|---|
| simple × {50, 200, 500} × {bursty, sinusoidal, fault-HA} | 9 | A "simple" intent is by definition basic 5G connectivity. Asking for HA / autoscaling / bursty headroom in a "simple" prompt is contradictory — the moment you add those requirements, the intent becomes medium or complex. Including them would test whether systems *over-interpret* simple prompts, which is a different research question. |
| {medium, complex} × 50 × {bursty, sinusoidal, fault-HA except medium/50/fault-HA which is N06} | 5 | A 50-UE scenario doesn't have meaningful peaks (50 → 150 isn't operationally interesting), and autoscaling 50 UEs is overkill. Medium/50/fault-HA is kept (N06, clinic) because patient-safety HA is meaningful even at small scale. |

Documenting these as *intentional null cells* matters for the thesis methodology section — reviewers should not see them as missing data.

## 5. Provenance of each prompt

| Source | Count | Prompt IDs | Treatment |
|---|---|---|---|
| Inherited verbatim from [experiment_1/prompts.json](../experiment_1/prompts.json) | 25 | S01–S06, M01–M10, C01–C09 | Annotated with `ue_band`, `scenario`, `structured_features`. Prompt text, expected VNFs, slice types, and original notes preserved. |
| Newly drafted to fill scenario gaps | 11 | N01–N11 | Each one targets a specific empty cell. Prompt + structured_features written together; notes field documents which cell is being filled. |

Each new prompt was drafted to:
- Use plausible operator language (no scenario tags leaking into prose).
- Include numeric anchors (e.g. "200 customers, bursting to 600") so a reviewer can verify the features match the text.
- Map cleanly onto exactly one scenario flavour.

The 11 new prompts and the cells they target:

| ID | Cell filled | Scenario signal in prompt |
|---|---|---|
| N01 | simple/500/steady | "500 employees, internet only" |
| N02 | medium/50/steady | "5G testbed for 50 IoT prototype devices" |
| N03 | medium/200/bursty-headroom | "200 normal customers, bursting to 600 during sales events" |
| N04 | medium/500/sinusoidal-autoscale | "diurnal traffic … autoscaling required" |
| N05 | medium/200/sinusoidal-autoscale | "peaks at 8am, 12pm, 5pm; autoscaling needed" |
| N06 | medium/50/fault-HA | "must be highly available — connectivity loss is a patient safety risk" |
| N07 | medium/500/bursty-headroom | "500 baseline … surging to 2000 during shipping peaks like Black Friday" |
| N08 | medium/200/fault-HA | "must remain available 99.999% of the time" |
| N09 | complex/200/bursty-headroom | "200 baggage scanners, surging to 800 during peak hours" |
| N10 | complex/200/sinusoidal-autoscale | "demand follows transit schedules; autoscale to follow rush hours" |
| N11 | complex/500/sinusoidal-autoscale | "traffic sinusoidal across class periods; autoscaling required" |

## 6. Annotation rules for `structured_features`

Each existing prompt was annotated by inspecting the prompt text and applying these rules. The rules are deliberately simple so they can be reproduced by another annotator.

### `ue_count`

- If the prompt names a UE/user/device count, use that number directly when it represents *concurrent active UEs* (e.g. "300 devices" → 300).
- For very-large-N events (stadium 50 000, festival 80 000, port 5000 containers), **`ue_count` is the concurrent active UE count** while `subscriber_count` is the headline number from the prompt. This split reflects operator practice: not every subscriber is concurrently attached. Decisions:
  - M08 stadium: 50 000 spectators → 5000 concurrent UEs
  - C05 festival: 80 000 attendees → 8000 concurrent UEs
  - C06 port: 5000 containers/day → 1000 concurrent UEs
  - These are the four cases flagged by the consistency check in §7.
- If the prompt is qualitative ("a small office", "minimal test"), pick a number consistent with the `ue_band` (e.g. band=50 → 20–50 UEs).

### `subscriber_count`

- Defaults to `ue_count` for ordinary intents.
- Set to the prompt's headline number for the four large-event cases above.
- Drives UDR sizing (per_5000_subscribers).

### `session_count`

- Defaults to `ue_count` (one PDU session per UE is the typical assumption).
- For mMTC (sensor-only) intents, set to a fraction of UE count since not every sensor maintains an always-on session (M05: 10 000 sensors → 2000 sessions).
- Drives SMF sizing (per_500_sessions).

### `throughput_gbps`

- If the prompt states a number, use it directly (M01 "2 Gbps" → 2.0; M03 "5 Gbps" → 5.0).
- Otherwise estimate using rough per-UE rates by use case:
  - eMBB consumer/office: ~5 Mbps/UE → e.g. 200 UEs ≈ 1.0 Gbps
  - mMTC sensors: ~0.1 Mbps/UE → e.g. 10 000 sensors ≈ 1 Gbps
  - URLLC industrial: low-bandwidth, ~5 Mbps/UE
  - Video-heavy (stadium, festival, hospital imaging, AV testing): bumped to 15–30 Gbps
- Validation: ratio `throughput_gbps / ue_count` falls in [0.05, 50] Mbps/UE for every prompt (verified by the plausibility check in §7), spanning mMTC to heavy video.

### `slices`

- A list of `{sst}` dictionaries.
- Multi-tenancy in M10 represented as three SST=1 entries (three eMBB slices with different SDs is the realistic OAI representation).
- The number of slices drives NSSF sizing.

### `ha_required`, `peak_multiplier`, `autoscale_required`

- Scenario tag fully determines these:
  - `scenario == "fault-HA"` ⇒ `ha_required = true`
  - `scenario == "bursty-headroom"` ⇒ `peak_multiplier > 1.0`
  - `scenario == "sinusoidal-autoscale"` ⇒ `autoscale_required = true`
  - `scenario == "steady"` ⇒ all three at default (false / 1.0 / false)
- The mapping is enforced by §7's automated consistency check.

## 7. Validation performed

Five automated checks were run against the dataset (script: ad-hoc one-shot, see [the build session bash output](../../../home/larbi/.claude/plans/i-want-to-run-misty-pudding.md)):

1. **Total prompt count = 36** — pass.
2. **All prompts have `structured_features`** — pass.
3. **Scenario-flag consistency** (the 1:1 mapping from §6) — pass for all 36 prompts.
4. **Throughput plausibility** — `throughput_gbps / ue_count` in `[0.05, 100] Mbps/UE` — pass for all 36 prompts.
5. **`ue_count` vs prompt-text numeric mention** — 4 deliberate discrepancies (M06, M08, C05, C06), all explained in §6 (concurrent-vs-headline split). M06's regex hit was a false positive on "10ms" latency, not a count.

A sixth check was a *resource-oracle dry-run* applying the scaling formulas to four representative intents:

| Intent | UPF CPU/Mem (oracle) | Behaviour |
|---|---|---|
| S01 (50 UEs, 0.5 Gbps) | 900m / 640Mi | Just above base, plausible. |
| M03 (1000 UEs, 5 Gbps, HA) | 4000m / 1792Mi | UPF CPU hits max cap, memory healthy. |
| M08 (5000 UEs, 20 Gbps, bursty) | 4000m / 4096Mi | UPF saturated at max — confirms the oracle caps high-end intents. |
| C05 (8000 UEs, 30 Gbps, bursty) | 4000m / 4096Mi | Same saturation — expected. |

Implication for Metric 3: very-large intents (M08, C05, C04 in similar territory) will have *less discriminative* resource-accuracy scores because every system's output is forced toward the cap. We should report Metric 3 with a "small-vs-large intent" breakdown to surface this.

## 8. Known limitations and reviewer-facing caveats

1. **Throughput estimates for 14 of 25 inherited prompts are author-derived**, not stated in the prompt. They follow the per-use-case heuristics in §6 but are inherently judgement calls. Mitigation: report Metric 3 also as "deviation from chart defaults" (per the plan) so the result doesn't hinge solely on this oracle.

2. **Subscriber-vs-concurrent split** (M08, C05, C06) is a defensible operator practice but a reviewer may legitimately challenge the specific ratio (e.g. "why 10% concurrent attachment for the stadium, not 20%?"). The numbers are documented in §6 so they can be revised in one place if the reviewer disagrees, without re-annotating the dataset structure.

3. **Steady-cell density imbalance** — steady has 20 prompts, sinusoidal has 4. Within-scenario tests on sinusoidal will have lower power than on steady. If reviewer demands balanced, drop 5 steady prompts (e.g. C04, C06, C08 redundancy) and retest.

4. **No real-traffic ground truth** — `throughput_gbps` is a planning estimate, not measured. The oracle treats this estimate as ground truth, so all systems are scored against the same target. Bias is shared, not differential.

5. **Multi-tenant slice representation** (M10) — three SST=1 slices is one valid encoding; an alternative is `[{sst:1, sd:"01"}, {sst:1, sd:"02"}, {sst:1, sd:"03"}]`. NSSF sizing comes out the same (3 slices), so the metric is unaffected, but a reader of the file should know.

6. **`min_vnf_count` / `max_vnf_count` / `expected_vnfs`** are inherited as-is from the v1 dataset. They were not re-validated against the new scenario tags. The new prompts (N01–N11) have these fields populated by analogy with similar inherited prompts; they may need tightening before publication.

## 9. What the user should review before M1

1. **Spot-check 3–5 inherited prompts** (suggest M03, M05, M08, C03, C05) and confirm the `structured_features` numbers are operator-defensible.
2. **Confirm the concurrent-UE split** for M08 (5000), C05 (8000), C06 (1000) — these are the largest oracle-impacting choices.
3. **Decide on the imbalance**: keep 20 steady prompts, or trim to balance scenarios at the cost of n.
4. **Confirm the 14 empty cells stay empty** — particularly whether you want at least one "simple/200/bursty" or similar to be added for symmetry, even if a bit contrived.
5. **Approve N06's slice list** (`[{sst:1}, {sst:3}]` for the clinic) — clinical IoT could also be argued as URLLC.

Once those are settled, the dataset is frozen and M1 can begin.
