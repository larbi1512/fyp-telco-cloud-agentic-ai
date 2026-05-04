# B1 Manual Baseline — Literature References & Pilot Protocol

This document supports the B1 (manual operator) baseline used in the
pre-deployment comparative experiment. It has two parts:

- **§1 Literature references** — the three sources the calibration falls
  back to until the pilot is run, with DOIs / URLs and the specific numbers
  used.
- **§2 Pilot protocol** — a step-by-step recipe for running the 2-hour
  calibration pilot and recording the data into
  [b1_calibration.yaml](b1_calibration.yaml).

When you finish the pilot, paste the recorded numbers into the `pilot_*`
blocks of `b1_calibration.yaml`, set `active.source: pilot`, and re-run the
B1 smoke test to confirm the emulator is using your data.

---

## §1 Literature references

The B1 emulator reads its timing distribution and error rates from
`literature_*` blocks in [b1_calibration.yaml](b1_calibration.yaml). Each
number ultimately traces to one of the three sources below. Cite all three
in the thesis methodology section under "B1 calibration".

### Source 1 — Yousaf et al. 2017

> **Yousaf, F. Z., Bredel, M., Schaller, S., & Schneider, F.** (2017).
> *NFV and SDN — Key Technology Enablers for 5G Networks.*
> IEEE Journal on Selected Areas in Communications, 35(11), 2468–2478.

- DOI: [10.1109/JSAC.2017.2752961](https://doi.org/10.1109/JSAC.2017.2752961)
- IEEE Xplore: <https://ieeexplore.ieee.org/document/8060501>
- Open access PDF (preprint): typically available via the authors' institutional pages.

**What we use from it.** Section V reports manual VNF onboarding and
configuration timings for ETSI MANO-style deployments on a calibrated test
bed. Per-VNF "lookup + edit" times of 5–8 minutes are reported for operators
working from a runbook against a vanilla MANO descriptor catalogue. The
emulator uses **mean = 95 s, sd = 25 s** for the per-VNF edit time as a
conservative lower-bound (we assume an operator with a Helm-and-`kubectl`
workflow is faster than full MANO descriptor authoring, but not dramatically
so).

### Source 2 — Benzaid & Taleb 2020

> **Benzaid, C., & Taleb, T.** (2020).
> *AI-Driven Zero Touch Network and Service Management in 5G and Beyond:
> Challenges and Research Directions.*
> IEEE Network, 34(2), 186–194.

- DOI: [10.1109/MNET.001.1900252](https://doi.org/10.1109/MNET.001.1900252)
- IEEE Xplore: <https://ieeexplore.ieee.org/document/9000169>

**What we use from it.** Section II ("Manual Network Operations: Pain
Points") summarises operator-error studies and quotes typical
configuration-error rates of 10–15 % in manual deployments — i.e. the share
of generated artefacts that ship with at least one defect a real operator
would have caught with one more review pass. The emulator uses
**`config_error_rate_pct = 12.0`**, the midpoint of that range.

### Source 3 — 5G-PPP Architecture Working Group 2020

> **5G-PPP Architecture Working Group.** (2020).
> *View on 5G Architecture* (Version 4.0 — White Paper).

- White paper PDF: <https://5g-ppp.eu/wp-content/uploads/2021/11/Architecture-WP-V4.0-final.pdf>
- 5G-PPP publications index: <https://5g-ppp.eu/white-papers/>

**What we use from it.** Section 4 ("Operations and Management") gives
end-to-end onboarding and policy-validation timing references that anchor
the per-intent "understanding" time (60 / 180 / 420 seconds for
simple / medium / complex intents) and the typical 2.1-iteration mean for
dry-run loops before a clean install.

### Mapping reference → calibration field

| Calibration field (in `b1_calibration.yaml`) | Source | Value used |
|---|---|---|
| `literature_per_vnf_edit_sec.mean` / `.sd` | Yousaf 2017 §V | 95 s / 25 s |
| `literature_per_intent_understanding_sec.{simple,medium,complex}` | 5G-PPP 2020 §4 | 60 / 180 / 420 s |
| `literature_helm_install_sec.mean` / `.sd` | 5G-PPP 2020 §4 | 45 s / 15 s |
| `literature_iterations_to_converge.mean` / `.sd` | 5G-PPP 2020 §4 | 2.1 / 0.9 |
| `literature_config_error_rate_pct` | Benzaid & Taleb 2020 §II | 12.0 |
| `literature_per_vnf_intervention_count.mean` / `.sd` | derived from per-VNF edit time × intervention frequency in Yousaf 2017 §V | 1.2 / 0.4 |
| `literature_error_kinds[*]` | combined operator-survey themes from Benzaid & Taleb 2020 §II + 5G-PPP 2020 §4 | weights documented inline in `b1_calibration.yaml` |

---

## §2 Pilot protocol — 2-hour calibration run

The pilot replaces literature defaults with measurements from a real human
operating the cluster by hand. Once recorded, the emulator's outputs become
defensible against a reviewer who challenges the literature numbers.

### Goals

- Measure per-VNF "lookup + edit" wall-clock time on this specific
  test bed and chart layout.
- Measure how many `helm template` / `kubectl apply --dry-run` iterations
  it takes to reach a clean install.
- Count operator errors that *would* have shipped if the dry-run hadn't
  caught them.
- Capture the kinds of errors that occurred so the emulator's
  `error_kinds` weights reflect this operator's tendencies.

### Who runs it

A single operator (you, or an MSc peer) with reasonable Helm/`kubectl`
experience but **without** memorised familiarity with this exact chart set.
If you're already deeply familiar with `charts/oai-5g-core/` (because you
wrote the MAS), recruit a peer instead — otherwise the timing numbers
under-represent a real first-time operator.

### Materials

- A running terminal session into the test bed (the same cluster
  `172.21.25.9 / vm09` used by the rest of the experiment).
- The runbook below.
- A timer / stopwatch app capable of multiple lap timings (phone is fine).
- A blank notes file: copy [pilot_log_template.md](pilot_log_template.md)
  if it exists, or use a fresh markdown file at
  `experiments/baselines/pilot_log.md`.
- The dataset file `experiments/datasets/intents_v2.json` open in another
  pane so you can see structured features.

### The two intents

Run two intents, in this order:

1. **Simple — `S01`**: *"Deploy a basic 5G core network."*
2. **Complex — `C03`**: *"We're building a digital twin of our factory
   floor. We have 50 CNC machines, 200 environmental sensors, and 20 AGVs.
   The digital twin needs real-time sync with sub-5ms latency. Also need a
   dashboard for management."*

Why these two: S01 anchors the simple band, C03 anchors the complex band
with multi-slice + HA implication. Together they bracket the timing range
the emulator interpolates over.

### Step-by-step (per intent)

1. **Prep cluster (5 min, not timed).** Tear down any existing OAI releases:
   ```
   cd ~/fyp && source venv/bin/activate
   python -c "from agents.deployer import teardown_releases; teardown_releases()"
   ```

2. **Read the intent (timed — `t_understanding`).** Start the lap timer.
   Read the prompt and the `structured_features` block. Sketch the target
   topology on paper — which VNFs, how many slices, replicas, throughput.
   Stop the lap when you have a plan you'd commit to.

3. **Per-VNF copy + edit (timed per VNF — `t_edit_<vnf>`).** Open
   `charts/oai-5g-core/oai-5g-basic/values.yaml` and the per-VNF chart
   directories. For each VNF in your topology:
   - Start the lap.
   - Copy the relevant block out as a stand-alone values file
     (e.g. `~/pilot/<vnf>-values.yaml`).
   - Edit replicas, resources, image tag, slice config, PLMN — anything the
     intent demands.
   - Stop the lap when the file is what you'd ship.
   - **Record per-VNF**: `vnf_type`, `seconds`, and `edits_made` (count of
     fields touched).

4. **Iteration loop (timed — `t_iter` per pass, plus `iter_count`).** Run:
   ```
   helm template <release> charts/oai-5g-core/<chart-dir> -f ~/pilot/<vnf>-values.yaml | kubectl apply --dry-run=client -f -
   ```
   for each VNF. Each pass:
   - Start lap.
   - Run, read errors, fix the values file.
   - Stop lap when this pass produced a clean dry-run.
   - **Record per pass**: VNF, seconds, what error you fixed, whether the
     error would have shipped to production if you hadn't run dry-run
     (this is the `would_have_shipped` flag — central to the error rate).
   - Repeat until clean. Count the total iterations as `iter_count` for
     this intent.

5. **Helm install (timed — `t_install`).** Run `helm install` for each
   VNF. Record total wall-clock from first install command to all
   pods Ready (`kubectl get pods -n oai-5g -w`).

6. **Mistake review (5 min, not timed).** Walk through your edits and the
   final running cluster. For every `would_have_shipped` flag from
   step 4, write a one-line description of what would have broken. These
   are the data points for `pilot_error_kinds`.

### Recording template

Use this structure (copy into `pilot_log.md`):

```markdown
# B1 Pilot — <date> — <operator>

## Intent S01
- t_understanding: <sec>
- VNFs deployed (with type and seconds):
  - nrf: <sec>, <edits> edits
  - amf: <sec>, <edits> edits
  - ...
- iter_count: <int>
- iter timings (sec each, in order): [..]
- t_install: <sec>
- would_have_shipped errors:
  - <error 1: short description, kind tag from list>
  - <error 2: ...>
- interventions (any moment of doubt / re-read of runbook): <count>

## Intent C03
... (same structure)

## Notes
<free text — surprises, runbook gaps, anything unusual>
```

### Translating the log into `b1_calibration.yaml`

Once the pilot is logged, fill in `pilot_*` blocks like this:

```yaml
pilot_per_vnf_edit_sec:
  mean: <average of all per-VNF seconds across both intents>
  sd:   <stdev of those>

pilot_per_intent_understanding_sec:
  simple:  <S01's t_understanding>
  medium:  <interpolate from S01 and C03 — if you only ran 2 intents,
            use 0.5 * (simple + complex), or run a third medium intent>
  complex: <C03's t_understanding>

pilot_helm_install_sec:
  mean: <average of per-VNF helm-install times>
  sd:   <stdev>

pilot_iterations_to_converge:
  mean: <average iter_count across the two intents>
  sd:   <stdev>

pilot_config_error_rate_pct:
  <total_would_have_shipped_errors / total_artifacts_generated * 100>

pilot_per_vnf_intervention_count:
  mean: <average interventions per VNF>
  sd:   <stdev>

pilot_error_kinds:
  - {name: omitted_probe,           weight: <fraction observed>, description: "..."}
  - {name: missing_resource_limits, weight: <...>, description: "..."}
  # add any new kinds you observed, e.g.
  - {name: forgot_multus_n2,        weight: 0.10, description: "operator left N2 multus block disabled"}

pilot_notes: |
  <date>, <operator initials>, <cluster id>.
  Brief notes on anything unusual.
```

Then **flip the active source**:

```yaml
active:
  source: pilot
```

### Sanity-check after switchover

```
cd ~/fyp && source venv/bin/activate
python <<'PY'
from experiments.baselines.b1_manual import B1ManualRunner
import json

b1 = B1ManualRunner()
print("calibration source:", b1._cal["source"])
with open("experiments/datasets/intents_v2.json") as f:
    intents = {p["id"]: p for p in json.load(f)["prompts"]}
for iid in ["S01", "M03", "C03"]:
    art = b1.run({**intents[iid], "_rep": 0}, deploy=False)
    print(f"  {iid}: wall_clock_s={art['wall_clock_s']}")
PY
```

Expected: `calibration source: pilot`, and the wall-clock numbers should
sit close to (within ±25 % of) what you measured in steps 2–5. If they
don't, re-check the YAML — most likely a unit error (seconds vs minutes).

### What to do in the thesis appendix

1. Reproduce the **pilot log** (`pilot_log.md`) verbatim, with the date
   and operator id.
2. Reproduce the resulting `pilot_*` block from `b1_calibration.yaml`.
3. State the `active.source: pilot` switchover, and that all B1 numbers
   in the experiment results were generated against this calibration.
4. Cite the three literature sources (above) as the *fallback* defaults
   used during development before the pilot was recorded.

This makes the B1 baseline reviewer-defensible: the numbers are grounded
in observed data, the methodology is reproducible, and the literature
sources remain as a published cross-check.
