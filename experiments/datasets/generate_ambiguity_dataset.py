#!/usr/bin/env python3
"""Generate intents_v3_ambiguity.json — 300 intents for the external-validity
robustness study.

  P001-P100  perfect     — precise, unambiguous, all fields explicit
  A001-A100  ambiguous   — same structured_features but prompts use weird
                           synonyms for throughput / latency / device-count
  I001-I050  incomplete  — ue_count omitted from prompt; oracle value set to
                           the "reasonable default" the planner must infer
  X001-X050  contradictory — prompt says HA / N+1 / fault-tolerant but also
                           says "single replica" / "no redundancy"

Run:
    python experiments/datasets/generate_ambiguity_dataset.py
Outputs:
    experiments/datasets/intents_v3_ambiguity.json
"""

from __future__ import annotations
import json
from pathlib import Path

OUT = Path(__file__).parent / "intents_v3_ambiguity.json"

# ─── helpers ─────────────────────────────────────────────────────────────────

def sf(ue, thr, sess, subs, slices, ha, peak=1.0, auto=False):
    return dict(
        ue_count=ue, throughput_gbps=thr, session_count=sess,
        subscriber_count=subs,
        slices=[{"sst": s} for s in slices],
        ha_required=ha, peak_multiplier=peak, autoscale_required=auto,
    )

def ev(slices, ha=False):
    base = ["nrf", "amf", "smf", "upf", "ausf", "udm", "udr"]
    if len(set(slices)) > 1 or max(slices) == 3:
        base += ["nssf"]
    elif len(slices) == 1 and slices[0] != 1:
        base += ["nssf"]
    return sorted(set(base))

def ubnd(ue):
    if ue <= 100:   return 50
    if ue <= 500:   return 200
    return 500

SLICE_NAMES = {1: "eMBB", 2: "URLLC", 3: "mMTC"}

# ─── PERFECT intents P001-P100 ────────────────────────────────────────────────

PERFECT_SPECS = [
    # id-suffix, prompt, complexity, ue, thr, sess, subs, slices, ha, peak, auto, scenario
    # --- simple / steady ---
    ("P001", "Deploy a 5G core for 50 devices with 0.5 Gbps throughput.", "simple", 50, 0.5, 50, 50, [1], False, 1.0, False, "steady"),
    ("P002", "Set up a standalone 5G network for 80 users, eMBB only.", "simple", 80, 0.8, 80, 80, [1], False, 1.0, False, "steady"),
    ("P003", "Deploy 5G SA core for a 100-user office, internet access.", "simple", 100, 1.0, 100, 100, [1], False, 1.0, False, "steady"),
    ("P004", "Create a basic 5G core for a 40-user lab with eMBB slice.", "simple", 40, 0.4, 40, 40, [1], False, 1.0, False, "steady"),
    ("P005", "Deploy 5G for 60 smartphones with 0.6 Gbps aggregate throughput.", "simple", 60, 0.6, 60, 60, [1], False, 1.0, False, "steady"),
    ("P006", "Provision a minimal 5G core for 30 test devices at 0.3 Gbps.", "simple", 30, 0.3, 30, 30, [1], False, 1.0, False, "steady"),
    ("P007", "Set up 5G for a 200-user enterprise building, 1.5 Gbps throughput.", "simple", 200, 1.5, 200, 200, [1], False, 1.0, False, "steady"),
    ("P008", "Deploy a 5G core for a 150-user retail chain, 1.2 Gbps, eMBB.", "simple", 150, 1.2, 150, 150, [1], False, 1.0, False, "steady"),
    ("P009", "Create a 5G network for a 500-user hotel with 2.5 Gbps.", "simple", 500, 2.5, 500, 500, [1], False, 1.0, False, "steady"),
    ("P010", "Deploy a 5G testbed for 25 prototype devices at 0.2 Gbps.", "simple", 25, 0.2, 25, 25, [1], False, 1.0, False, "steady"),
    # --- simple / bursty ---
    ("P011", "Deploy 5G for a 200-person conference hall that peaks at 3× normal load.", "simple", 200, 1.0, 200, 200, [1], False, 3.0, False, "bursty-headroom"),
    ("P012", "Set up 5G for a 300-seat cinema, traffic spikes 5× at showtime.", "simple", 300, 1.5, 300, 300, [1], False, 5.0, False, "bursty-headroom"),
    ("P013", "Deploy 5G for a 100-device showroom; bursty traffic peaks 4× baseline.", "simple", 100, 0.5, 100, 100, [1], False, 4.0, False, "bursty-headroom"),
    # --- simple / fault-HA ---
    ("P014", "Deploy a high-availability 5G core for 50 critical devices, 0.5 Gbps.", "simple", 50, 0.5, 50, 50, [1], True, 1.0, False, "fault-HA"),
    ("P015", "Set up a fault-tolerant 5G network for 100 users with N+1 redundancy.", "simple", 100, 1.0, 100, 100, [1], True, 1.0, False, "fault-HA"),
    # --- medium / steady ---
    ("P016", "Deploy a 5G core for 500 UEs with 2 Gbps throughput, eMBB and URLLC slices.", "medium", 500, 2.0, 500, 500, [1,2], False, 1.0, False, "steady"),
    ("P017", "Deploy 5G for a factory with 300 robots needing URLLC, under 5 ms latency, 1.5 Gbps.", "medium", 300, 1.5, 300, 300, [2], False, 1.0, False, "steady"),
    ("P018", "Set up a 5G IoT network for 5000 sensors on mMTC slice, 1 Gbps aggregate.", "medium", 5000, 1.0, 1000, 5000, [3], False, 1.0, False, "steady"),
    ("P019", "Deploy 5G for 1000 university students, eMBB, 4 Gbps, no HA needed.", "medium", 1000, 4.0, 1000, 1000, [1], False, 1.0, False, "steady"),
    ("P020", "Create a multi-slice 5G core: eMBB for 400 users and mMTC for 2000 sensors.", "medium", 400, 2.0, 400, 2000, [1,3], False, 1.0, False, "steady"),
    ("P021", "Deploy 5G for a distribution warehouse, 200 handheld scanners on eMBB, 1 Gbps.", "medium", 200, 1.0, 200, 200, [1], False, 1.0, False, "steady"),
    ("P022", "Set up a private 5G network for 250 AR headsets on eMBB, 5 Gbps total.", "medium", 250, 5.0, 250, 250, [1], False, 1.0, False, "steady"),
    ("P023", "Deploy 5G for 400 drones requiring URLLC slice and 2 Gbps command channel.", "medium", 400, 2.0, 400, 400, [2], False, 1.0, False, "steady"),
    ("P024", "Create a 5G core for a hospital: 200 devices, eMBB+URLLC, 1 Gbps, no HA.", "medium", 200, 1.0, 200, 200, [1,2], False, 1.0, False, "steady"),
    ("P025", "Deploy 5G for a logistics hub with 600 UEs on eMBB, 3 Gbps throughput.", "medium", 600, 3.0, 600, 600, [1], False, 1.0, False, "steady"),
    # --- medium / bursty ---
    ("P026", "Set up 5G for a 5000-seat stadium, eMBB, traffic peaks 8× during events.", "medium", 5000, 10.0, 5000, 50000, [1], False, 8.0, False, "bursty-headroom"),
    ("P027", "Deploy 5G for a shopping mall with 3000 users, 2× peak on weekends.", "medium", 3000, 6.0, 3000, 3000, [1], False, 2.0, False, "bursty-headroom"),
    ("P028", "Create 5G for a concert venue: 10000 users, 3× peak during shows, eMBB.", "medium", 10000, 20.0, 10000, 10000, [1], False, 3.0, False, "bursty-headroom"),
    ("P029", "Deploy 5G for a transit hub: 2000 concurrent users, 4× morning/evening peaks.", "medium", 2000, 4.0, 2000, 2000, [1], False, 4.0, False, "bursty-headroom"),
    # --- medium / sinusoidal-autoscale ---
    ("P030", "Deploy 5G for a 500-user office with diurnal traffic pattern, autoscaling required.", "medium", 500, 2.0, 500, 500, [1], False, 2.0, True, "sinusoidal-autoscale"),
    ("P031", "Set up 5G for a 1000-user campus with daily traffic cycle, autoscale needed.", "medium", 1000, 4.0, 1000, 1000, [1], False, 2.0, True, "sinusoidal-autoscale"),
    ("P032", "Deploy 5G for a factory with sinusoidal shift changes, 300 devices, autoscale.", "medium", 300, 1.5, 300, 300, [1,2], False, 1.5, True, "sinusoidal-autoscale"),
    # --- medium / fault-HA ---
    ("P033", "Deploy a HA 5G core for 500 UEs, URLLC slice, N+1 replicas, 2 Gbps.", "medium", 500, 2.0, 500, 500, [2], True, 1.0, False, "fault-HA"),
    ("P034", "Set up a resilient 5G network for 300 critical sensors, HA, mMTC slice.", "medium", 300, 0.5, 300, 300, [3], True, 1.0, False, "fault-HA"),
    ("P035", "Deploy HA 5G for a hospital ICU: 150 devices, eMBB+URLLC, zero downtime.", "medium", 150, 1.5, 150, 150, [1,2], True, 1.0, False, "fault-HA"),
    ("P036", "Create a fault-tolerant 5G core for 1000 subscribers, eMBB, 4 Gbps, HA.", "medium", 1000, 4.0, 1000, 1000, [1], True, 1.0, False, "fault-HA"),
    ("P037", "Set up 5G with HA for a power grid with 200 URLLC endpoints, 1 Gbps.", "medium", 200, 1.0, 200, 200, [2], True, 1.0, False, "fault-HA"),
    # --- complex / steady ---
    ("P038", "Deploy 5G for a smart city district: 10000 IoT sensors (mMTC), 500 cameras (eMBB), 50 autonomous vehicles (URLLC). Aggregate: 15 Gbps.", "complex", 10550, 15.0, 2000, 10550, [1,2,3], False, 1.0, False, "steady"),
    ("P039", "Create a full 5G core for a tier-1 MNO testbed with 5000 UEs, tri-slice (eMBB/URLLC/mMTC), 20 Gbps.", "complex", 5000, 20.0, 5000, 5000, [1,2,3], False, 1.0, False, "steady"),
    ("P040", "Deploy 5G for an industrial campus: 800 URLLC endpoints for automation, 1200 eMBB devices for workers, 5000 mMTC sensors. 10 Gbps.", "complex", 7000, 10.0, 2000, 7000, [1,2,3], False, 1.0, False, "steady"),
    ("P041", "Set up a 5G network for a research university: 3000 student devices (eMBB), 500 lab instruments (URLLC), 0.5 Gbps per lab, 8 Gbps total.", "complex", 3500, 8.0, 3500, 3500, [1,2], False, 1.0, False, "steady"),
    ("P042", "Create 5G for a digital mine: 600 autonomous loaders (URLLC), 200 cameras (eMBB), 300 environmental sensors (mMTC), 4 Gbps.", "complex", 1100, 4.0, 1100, 1100, [1,2,3], False, 1.0, False, "steady"),
    ("P043", "Deploy a private 5G MNO for a port: 1000 dock workers (eMBB), 500 crane sensors (URLLC), 2000 container trackers (mMTC), 6 Gbps.", "complex", 3500, 6.0, 1500, 3500, [1,2,3], False, 1.0, False, "steady"),
    ("P044", "Set up 5G for a telehealth platform: 400 doctors (eMBB, 8K video), 100 surgical robots (URLLC, 1 ms latency), 2 Gbps.", "complex", 500, 2.0, 500, 500, [1,2], False, 1.0, False, "steady"),
    ("P045", "Deploy 5G for an oil refinery: 2000 URLLC sensors, 500 eMBB monitoring cameras, 3 Gbps, full auth stack.", "complex", 2500, 3.0, 2500, 2500, [1,2], False, 1.0, False, "steady"),
    # --- complex / bursty ---
    ("P046", "Deploy 5G for an Olympic stadium: 80000 spectators (eMBB), 500 staff radios (URLLC), 100× peak during opening ceremony.", "complex", 80500, 60.0, 80500, 80500, [1,2], False, 100.0, False, "bursty-headroom"),
    ("P047", "Create 5G for a music festival: 30000 attendees, eMBB, traffic 6× during headliner sets.", "complex", 30000, 40.0, 30000, 30000, [1], False, 6.0, False, "bursty-headroom"),
    ("P048", "Deploy 5G for a financial trading floor: 500 terminals (URLLC), 2× peak during market open/close.", "complex", 500, 5.0, 500, 500, [2], False, 2.0, False, "bursty-headroom"),
    # --- complex / sinusoidal-autoscale ---
    ("P049", "Deploy 5G for a smart grid: 50000 meters (mMTC), diurnal demand cycle, autoscale required, 5 Gbps peak.", "complex", 50000, 5.0, 10000, 50000, [3], False, 3.0, True, "sinusoidal-autoscale"),
    ("P050", "Set up 5G for a telecom PoP: 10000 subscribers, eMBB, sinusoidal daily load, autoscale, 40 Gbps peak.", "complex", 10000, 40.0, 10000, 10000, [1], False, 2.5, True, "sinusoidal-autoscale"),
    # --- complex / fault-HA ---
    ("P051", "Deploy a carrier-grade HA 5G core for 5000 UEs, tri-slice, 20 Gbps, N+1 on all VNFs.", "complex", 5000, 20.0, 5000, 5000, [1,2,3], True, 1.0, False, "fault-HA"),
    ("P052", "Create a geo-redundant 5G core for a national railway: 2000 URLLC endpoints, 500 eMBB devices, HA.", "complex", 2500, 3.0, 2500, 2500, [1,2], True, 1.0, False, "fault-HA"),
    ("P053", "Deploy 5G for a nuclear facility: 1000 URLLC safety sensors, 200 eMBB cameras, HA, zero tolerance for downtime.", "complex", 1200, 2.0, 1200, 1200, [1,2], True, 1.0, False, "fault-HA"),
    ("P054", "Set up HA 5G for an airport: 3000 passenger devices (eMBB), 500 baggage trackers (mMTC), 200 ATC radios (URLLC), 8 Gbps.", "complex", 3700, 8.0, 3700, 3700, [1,2,3], True, 1.0, False, "fault-HA"),
    ("P055", "Deploy a resilient 5G core for an autonomous vehicle testbed: 200 URLLC vehicles, 50 eMBB roadside units, HA.", "complex", 250, 1.5, 250, 250, [1,2], True, 1.0, False, "fault-HA"),
    # fill up to P100 with a systematic sweep
    ("P056", "Deploy 5G for 45 devices, 0.4 Gbps, eMBB.", "simple", 45, 0.4, 45, 45, [1], False, 1.0, False, "steady"),
    ("P057", "Set up 5G for 120 users, 1.1 Gbps, internet access.", "simple", 120, 1.1, 120, 120, [1], False, 1.0, False, "steady"),
    ("P058", "Deploy 5G for 350 workers, 1.8 Gbps, eMBB.", "medium", 350, 1.8, 350, 350, [1], False, 1.0, False, "steady"),
    ("P059", "Create 5G for 800 UEs, 3.5 Gbps, eMBB slice.", "medium", 800, 3.5, 800, 800, [1], False, 1.0, False, "steady"),
    ("P060", "Deploy a 5G network for 2000 UEs, 8 Gbps, eMBB+URLLC.", "complex", 2000, 8.0, 2000, 2000, [1,2], False, 1.0, False, "steady"),
    ("P061", "Set up 5G for 55 sensors on mMTC, 0.1 Gbps.", "simple", 55, 0.1, 55, 55, [3], False, 1.0, False, "steady"),
    ("P062", "Deploy 5G for 400 IoT devices, mMTC, 0.8 Gbps.", "medium", 400, 0.8, 400, 400, [3], False, 1.0, False, "steady"),
    ("P063", "Create 5G for 6000 sensors, mMTC+eMBB, 2 Gbps.", "complex", 6000, 2.0, 1200, 6000, [1,3], False, 1.0, False, "steady"),
    ("P064", "Deploy a 5G core for 75 URLLC devices, 0.5 Gbps, <1ms latency.", "simple", 75, 0.5, 75, 75, [2], False, 1.0, False, "steady"),
    ("P065", "Set up 5G for 250 URLLC endpoints, 1.2 Gbps, mission-critical.", "medium", 250, 1.2, 250, 250, [2], False, 1.0, False, "steady"),
    ("P066", "Deploy 5G for 60 devices, bursty, 3× peak, 0.3 Gbps baseline.", "simple", 60, 0.3, 60, 60, [1], False, 3.0, False, "bursty-headroom"),
    ("P067", "Set up 5G for 500 users, bursty, 5× peak, 2 Gbps baseline.", "medium", 500, 2.0, 500, 500, [1], False, 5.0, False, "bursty-headroom"),
    ("P068", "Deploy 5G for 2000 users, 10× peak, sinusoidal diurnal, 4 Gbps.", "complex", 2000, 4.0, 2000, 2000, [1], False, 10.0, True, "sinusoidal-autoscale"),
    ("P069", "Create 5G for 80 HA devices, URLLC, 0.4 Gbps, N+1.", "simple", 80, 0.4, 80, 80, [2], True, 1.0, False, "fault-HA"),
    ("P070", "Deploy HA 5G for 400 critical devices, eMBB, 2 Gbps, N+1.", "medium", 400, 2.0, 400, 400, [1], True, 1.0, False, "fault-HA"),
    ("P071", "Set up 5G for 90 users, 0.9 Gbps, eMBB, internet only.", "simple", 90, 0.9, 90, 90, [1], False, 1.0, False, "steady"),
    ("P072", "Deploy 5G for 160 students, 1.6 Gbps, eMBB.", "simple", 160, 1.6, 160, 160, [1], False, 1.0, False, "steady"),
    ("P073", "Create 5G for 700 enterprise users, 3 Gbps, eMBB+URLLC.", "medium", 700, 3.0, 700, 700, [1,2], False, 1.0, False, "steady"),
    ("P074", "Deploy 5G for 1500 subscribers, 6 Gbps, eMBB, autoscale.", "medium", 1500, 6.0, 1500, 1500, [1], False, 2.0, True, "sinusoidal-autoscale"),
    ("P075", "Set up 5G for 4000 devices, 16 Gbps, tri-slice, HA.", "complex", 4000, 16.0, 4000, 4000, [1,2,3], True, 1.0, False, "fault-HA"),
    ("P076", "Deploy 5G for 35 prototype devices, 0.35 Gbps, eMBB.", "simple", 35, 0.35, 35, 35, [1], False, 1.0, False, "steady"),
    ("P077", "Set up 5G for 180 users, 1.8 Gbps, eMBB slice.", "simple", 180, 1.8, 180, 180, [1], False, 1.0, False, "steady"),
    ("P078", "Deploy 5G for a 450-user campus, 2 Gbps, eMBB.", "medium", 450, 2.0, 450, 450, [1], False, 1.0, False, "steady"),
    ("P079", "Create 5G for 900 subscribers, 4 Gbps, eMBB+URLLC slices.", "medium", 900, 4.0, 900, 900, [1,2], False, 1.0, False, "steady"),
    ("P080", "Deploy 5G for 3500 UEs, 14 Gbps, eMBB+URLLC, HA required.", "complex", 3500, 14.0, 3500, 3500, [1,2], True, 1.0, False, "fault-HA"),
    ("P081", "Set up 5G for 65 IoT nodes, mMTC, 0.15 Gbps.", "simple", 65, 0.15, 65, 65, [3], False, 1.0, False, "steady"),
    ("P082", "Deploy 5G for 550 IoT devices, mMTC, 1.1 Gbps.", "medium", 550, 1.1, 550, 550, [3], False, 1.0, False, "steady"),
    ("P083", "Create 5G for 8000 IoT sensors, mMTC+eMBB, 3 Gbps.", "complex", 8000, 3.0, 1600, 8000, [1,3], False, 1.0, False, "steady"),
    ("P084", "Deploy 5G for 85 URLLC devices, 0.6 Gbps.", "simple", 85, 0.6, 85, 85, [2], False, 1.0, False, "steady"),
    ("P085", "Set up 5G for 350 URLLC automation endpoints, 1.8 Gbps.", "medium", 350, 1.8, 350, 350, [2], False, 1.0, False, "steady"),
    ("P086", "Deploy 5G, 70 users, bursty 4× peak, 0.4 Gbps baseline.", "simple", 70, 0.4, 70, 70, [1], False, 4.0, False, "bursty-headroom"),
    ("P087", "Set up 5G, 600 users, 6× bursty peak, 2.5 Gbps baseline.", "medium", 600, 2.5, 600, 600, [1], False, 6.0, False, "bursty-headroom"),
    ("P088", "Deploy 5G for 2500 users, diurnal pattern, autoscale, 5 Gbps.", "complex", 2500, 5.0, 2500, 2500, [1], False, 2.0, True, "sinusoidal-autoscale"),
    ("P089", "Create 5G for 90 HA URLLC devices, 0.5 Gbps.", "simple", 90, 0.5, 90, 90, [2], True, 1.0, False, "fault-HA"),
    ("P090", "Deploy HA 5G for 450 critical endpoints, eMBB+URLLC, 2.5 Gbps.", "medium", 450, 2.5, 450, 450, [1,2], True, 1.0, False, "fault-HA"),
    ("P091", "Set up 5G for 110 users, 1.1 Gbps, eMBB, steady state.", "simple", 110, 1.1, 110, 110, [1], False, 1.0, False, "steady"),
    ("P092", "Deploy 5G for 750 enterprise users, 3.5 Gbps, eMBB.", "medium", 750, 3.5, 750, 750, [1], False, 1.0, False, "steady"),
    ("P093", "Create 5G for 4500 devices, 18 Gbps, tri-slice.", "complex", 4500, 18.0, 4500, 4500, [1,2,3], False, 1.0, False, "steady"),
    ("P094", "Deploy 5G for 1800 subscribers, 7 Gbps, eMBB, autoscale.", "medium", 1800, 7.0, 1800, 1800, [1], False, 2.0, True, "sinusoidal-autoscale"),
    ("P095", "Set up HA 5G for 600 critical users, 3 Gbps, eMBB.", "medium", 600, 3.0, 600, 600, [1], True, 1.0, False, "fault-HA"),
    ("P096", "Deploy 5G for a 5000-device smart building, mMTC+eMBB, 4 Gbps, HA.", "complex", 5000, 4.0, 5000, 5000, [1,3], True, 1.0, False, "fault-HA"),
    ("P097", "Create 5G for a telecom lab: 200 devices, all slices, 2 Gbps.", "medium", 200, 2.0, 200, 200, [1,2,3], False, 1.0, False, "steady"),
    ("P098", "Deploy 5G for a port authority: 1000 URLLC cranes + 500 eMBB staff, 3 Gbps.", "complex", 1500, 3.0, 1500, 1500, [1,2], False, 1.0, False, "steady"),
    ("P099", "Set up 5G for 2200 stadium visitors, bursty 7× peak, eMBB, 6 Gbps.", "complex", 2200, 6.0, 2200, 2200, [1], False, 7.0, False, "bursty-headroom"),
    ("P100", "Deploy 5G for 300 HA URLLC safety devices, 1.5 Gbps, N+1 redundancy.", "medium", 300, 1.5, 300, 300, [2], True, 1.0, False, "fault-HA"),
]

# ─── AMBIGUOUS synonyms ───────────────────────────────────────────────────────
# Each ambiguous prompt reuses the same structured_features as the matching
# perfect intent (A001 mirrors P001, etc.) but the text uses non-standard
# vocabulary the parser must resolve.

THROUGHPUT_SYNONYMS = [
    "data pipe capacity of {v} Gbps",
    "aggregate bandwidth envelope of {v} Gbps",
    "bits-per-second budget of {v} Gbps",
    "throughput pipe of {v} Gbps",
    "raw channel capacity of {v} Gbps",
    "wire-speed ceiling of {v} Gbps",
    "pipe width of {v} Gbps",
    "data-rate envelope of {v} Gbps",
    "bandwidth footprint of {v} Gbps",
    "spectral throughput of {v} Gbps",
]

UE_SYNONYMS = [
    "{n} gadgets",
    "{n} endpoints",
    "{n} kit units",
    "{n} wireless nodes",
    "{n} subscriber terminals",
    "{n} radio endpoints",
    "{n} attached devices",
    "{n} connected kit",
    "{n} client nodes",
    "{n} wireless clients",
]

LATENCY_SYNONYMS = [
    "response snappiness under 5 ms",
    "lag budget capped at 5 ms",
    "round-trip ceiling of 5 ms",
    "time-to-respond under 5 ms",
    "packet flight time under 5 ms",
]

HA_SYNONYMS = [
    "geo-resilient",
    "fault-hardened",
    "five-nines availability",
    "non-stop operation",
    "always-on resilience",
    "zero-failover tolerance",
    "carrier-grade redundancy",
    "no single point of failure",
    "continuous uptime guarantee",
    "blast-zone survivability",
]

SLICE_SYNONYMS = {
    1: ["broadband slice", "video slice", "eMBB fabric", "high-bandwidth channel", "broadband carrier"],
    2: ["low-latency lane", "deterministic pipe", "mission-critical slice", "URLLC fabric", "real-time channel"],
    3: ["sensor mesh", "massive-IoT fabric", "mMTC carrier", "device soup", "sensor swarm slice"],
}

def ambiguous_prompt(spec):
    _, _, complexity, ue, thr, _, _, slices, ha, peak, auto, scenario = spec
    idx = int(spec[0][1:]) - 1  # 0-based index into synonym lists
    thr_syn = THROUGHPUT_SYNONYMS[idx % len(THROUGHPUT_SYNONYMS)].format(v=thr)
    ue_syn  = UE_SYNONYMS[idx % len(UE_SYNONYMS)].format(n=ue)
    ha_syn  = HA_SYNONYMS[idx % len(HA_SYNONYMS)]
    slice_labels = [SLICE_SYNONYMS[s][idx % len(SLICE_SYNONYMS[s])] for s in slices]
    slice_str = " and ".join(slice_labels)

    if scenario == "bursty-headroom":
        burst_str = f" Traffic spikes {int(peak)}× at peak."
    elif scenario == "sinusoidal-autoscale":
        burst_str = " Traffic follows a sinusoidal diurnal pattern; elasticity required."
    elif scenario == "fault-HA":
        burst_str = f" Deployment must be {ha_syn}."
    else:
        burst_str = ""

    return (f"Spin up a 5G core for {ue_syn} riding on a {slice_str} "
            f"with a {thr_syn}.{burst_str}")

# Build ambiguous specs as clones of perfect but with new prompt
AMBIGUOUS_SPECS = []
for spec in PERFECT_SPECS:
    aid = "A" + spec[0][1:]  # P001 → A001
    new_prompt = ambiguous_prompt(spec)
    AMBIGUOUS_SPECS.append((aid, new_prompt) + spec[2:])

# ─── INCOMPLETE intents I001-I050 ─────────────────────────────────────────────
# Operator forgets to state UE count. Oracle ue_count set to "reasonable default"
# the MAS planner must infer from context. We mark ue_count=None in the JSON
# via a sentinel but keep structured_features for scoring (oracle still needs it).

INCOMPLETE_RAW = [
    # id, prompt (no mention of UE/device count), complexity, ue_oracle, thr, sess, subs, slices, ha, peak, auto, scenario
    ("I001", "Deploy a basic 5G core for internet access. Throughput needed: 0.5 Gbps.", "simple", 50, 0.5, 50, 50, [1], False, 1.0, False, "steady"),
    ("I002", "Set up a standalone 5G network with eMBB slice. Target throughput: 1 Gbps.", "simple", 100, 1.0, 100, 100, [1], False, 1.0, False, "steady"),
    ("I003", "Deploy a 5G core for an office, internet only. Aggregate bandwidth: 1.5 Gbps.", "simple", 200, 1.5, 200, 200, [1], False, 1.0, False, "steady"),
    ("I004", "Create a 5G testbed. Required throughput: 0.2 Gbps. eMBB slice.", "simple", 25, 0.2, 25, 25, [1], False, 1.0, False, "steady"),
    ("I005", "Deploy 5G for a retail store. Throughput budget: 0.8 Gbps. eMBB only.", "simple", 80, 0.8, 80, 80, [1], False, 1.0, False, "steady"),
    ("I006", "Set up 5G with URLLC for a warehouse automation. Need sub-5ms latency, 0.6 Gbps.", "medium", 100, 0.6, 100, 100, [2], False, 1.0, False, "steady"),
    ("I007", "Deploy 5G for a factory floor, URLLC slice required. Throughput: 1.5 Gbps.", "medium", 200, 1.5, 200, 200, [2], False, 1.0, False, "steady"),
    ("I008", "Create a 5G network for IoT deployments with mMTC slice. Throughput: 1 Gbps.", "medium", 2000, 1.0, 400, 2000, [3], False, 1.0, False, "steady"),
    ("I009", "Deploy dual-slice 5G (eMBB+URLLC). Aggregate throughput: 2 Gbps.", "medium", 300, 2.0, 300, 300, [1,2], False, 1.0, False, "steady"),
    ("I010", "Set up 5G for a university, eMBB, 4 Gbps. No HA needed.", "medium", 1000, 4.0, 1000, 1000, [1], False, 1.0, False, "steady"),
    ("I011", "Deploy a high-availability 5G core. Throughput: 0.5 Gbps, URLLC, N+1.", "simple", 50, 0.5, 50, 50, [2], True, 1.0, False, "fault-HA"),
    ("I012", "Set up fault-tolerant 5G, eMBB, N+1 replicas. Throughput requirement: 1 Gbps.", "simple", 100, 1.0, 100, 100, [1], True, 1.0, False, "fault-HA"),
    ("I013", "Create a resilient 5G core, eMBB, HA required. Aggregate bandwidth: 2 Gbps.", "medium", 300, 2.0, 300, 300, [1], True, 1.0, False, "fault-HA"),
    ("I014", "Deploy 5G with geo-redundancy, URLLC, 3 Gbps throughput.", "medium", 500, 3.0, 500, 500, [2], True, 1.0, False, "fault-HA"),
    ("I015", "Set up HA 5G with eMBB+URLLC. Total throughput: 4 Gbps.", "medium", 600, 4.0, 600, 600, [1,2], True, 1.0, False, "fault-HA"),
    ("I016", "Deploy 5G for bursty traffic, eMBB, 3× peak headroom, 1 Gbps baseline.", "simple", 100, 1.0, 100, 100, [1], False, 3.0, False, "bursty-headroom"),
    ("I017", "Create a 5G network for event-driven bursty loads. eMBB, 5× peak, 2 Gbps.", "medium", 500, 2.0, 500, 500, [1], False, 5.0, False, "bursty-headroom"),
    ("I018", "Set up 5G for high-peak events, 10× burst, eMBB, 4 Gbps baseline.", "complex", 2000, 4.0, 2000, 2000, [1], False, 10.0, False, "bursty-headroom"),
    ("I019", "Deploy 5G for a diurnal traffic pattern, autoscale required, eMBB, 2 Gbps.", "medium", 500, 2.0, 500, 500, [1], False, 2.0, True, "sinusoidal-autoscale"),
    ("I020", "Create autoscaling 5G for sinusoidal load, eMBB+URLLC, 5 Gbps peak.", "complex", 2000, 5.0, 2000, 2000, [1,2], False, 2.0, True, "sinusoidal-autoscale"),
    ("I021", "Deploy 5G for a smart city, tri-slice. Aggregate: 10 Gbps.", "complex", 5000, 10.0, 5000, 5000, [1,2,3], False, 1.0, False, "steady"),
    ("I022", "Set up 5G for industrial automation, URLLC. Throughput: 2 Gbps.", "medium", 300, 2.0, 300, 300, [2], False, 1.0, False, "steady"),
    ("I023", "Deploy 5G for a telehealth service, eMBB. Throughput: 3 Gbps.", "medium", 500, 3.0, 500, 500, [1], False, 1.0, False, "steady"),
    ("I024", "Create 5G for a mining operation, URLLC+eMBB. Throughput: 4 Gbps.", "complex", 1000, 4.0, 1000, 1000, [1,2], False, 1.0, False, "steady"),
    ("I025", "Set up a 5G network for logistics, eMBB slice, 2 Gbps.", "medium", 400, 2.0, 400, 400, [1], False, 1.0, False, "steady"),
    ("I026", "Deploy HA 5G for emergency services, URLLC. Bandwidth: 1.5 Gbps.", "medium", 200, 1.5, 200, 200, [2], True, 1.0, False, "fault-HA"),
    ("I027", "Set up 5G for a data centre, eMBB. Required throughput: 8 Gbps. HA.", "complex", 2000, 8.0, 2000, 2000, [1], True, 1.0, False, "fault-HA"),
    ("I028", "Deploy 5G for a transport network, URLLC, HA. Throughput: 3 Gbps.", "medium", 500, 3.0, 500, 500, [2], True, 1.0, False, "fault-HA"),
    ("I029", "Create 5G for a financial trading system, URLLC, 5 Gbps, HA.", "complex", 1000, 5.0, 1000, 1000, [2], True, 1.0, False, "fault-HA"),
    ("I030", "Set up 5G for a national power grid, mMTC+URLLC, 4 Gbps.", "complex", 10000, 4.0, 2000, 10000, [2,3], False, 1.0, False, "steady"),
    ("I031", "Deploy 5G, eMBB, bursty 2× peak, 0.5 Gbps baseline.", "simple", 50, 0.5, 50, 50, [1], False, 2.0, False, "bursty-headroom"),
    ("I032", "Set up 5G, eMBB, 4× burst, 1.5 Gbps baseline.", "medium", 300, 1.5, 300, 300, [1], False, 4.0, False, "bursty-headroom"),
    ("I033", "Deploy 5G, URLLC, 2 Gbps, latency under 1 ms.", "medium", 300, 2.0, 300, 300, [2], False, 1.0, False, "steady"),
    ("I034", "Create 5G, mMTC, 0.5 Gbps, massive connectivity.", "medium", 3000, 0.5, 600, 3000, [3], False, 1.0, False, "steady"),
    ("I035", "Set up 5G, eMBB+mMTC, 3 Gbps, mixed use.", "medium", 1000, 3.0, 1000, 1000, [1,3], False, 1.0, False, "steady"),
    ("I036", "Deploy 5G, eMBB, autoscale, sinusoidal load, 3 Gbps.", "medium", 600, 3.0, 600, 600, [1], False, 1.5, True, "sinusoidal-autoscale"),
    ("I037", "Set up 5G, URLLC+eMBB, autoscale, 6 Gbps peak.", "complex", 2000, 6.0, 2000, 2000, [1,2], False, 2.0, True, "sinusoidal-autoscale"),
    ("I038", "Deploy 5G for a hospital network, eMBB+URLLC, HA, 2 Gbps.", "medium", 400, 2.0, 400, 400, [1,2], True, 1.0, False, "fault-HA"),
    ("I039", "Create a carrier 5G network, eMBB, 20 Gbps, HA.", "complex", 5000, 20.0, 5000, 5000, [1], True, 1.0, False, "fault-HA"),
    ("I040", "Set up 5G for a nuclear plant, URLLC, HA, 2 Gbps.", "complex", 1000, 2.0, 1000, 1000, [2], True, 1.0, False, "fault-HA"),
    ("I041", "Deploy 5G for a port, eMBB+URLLC, 5 Gbps, no HA.", "complex", 2000, 5.0, 2000, 2000, [1,2], False, 1.0, False, "steady"),
    ("I042", "Set up 5G for a smart grid, mMTC, 2 Gbps, autoscale.", "complex", 20000, 2.0, 4000, 20000, [3], False, 1.5, True, "sinusoidal-autoscale"),
    ("I043", "Deploy 5G, all slices, HA, 15 Gbps.", "complex", 4000, 15.0, 4000, 4000, [1,2,3], True, 1.0, False, "fault-HA"),
    ("I044", "Create 5G for AR headsets, eMBB, 6 Gbps.", "medium", 500, 6.0, 500, 500, [1], False, 1.0, False, "steady"),
    ("I045", "Set up 5G for autonomous vehicles, URLLC, 3 Gbps.", "medium", 200, 3.0, 200, 200, [2], False, 1.0, False, "steady"),
    ("I046", "Deploy 5G for a drone fleet, URLLC, 1.5 Gbps.", "medium", 300, 1.5, 300, 300, [2], False, 1.0, False, "steady"),
    ("I047", "Set up 5G for a railway, URLLC+eMBB, HA, 3 Gbps.", "complex", 1000, 3.0, 1000, 1000, [1,2], True, 1.0, False, "fault-HA"),
    ("I048", "Deploy 5G for a stadium, eMBB, bursty 8× peak, 10 Gbps.", "complex", 10000, 10.0, 10000, 10000, [1], False, 8.0, False, "bursty-headroom"),
    ("I049", "Create 5G for a smart factory, all slices, 8 Gbps.", "complex", 3000, 8.0, 3000, 3000, [1,2,3], False, 1.0, False, "steady"),
    ("I050", "Set up 5G for a trading platform, URLLC, HA, 4 Gbps.", "complex", 500, 4.0, 500, 500, [2], True, 1.0, False, "fault-HA"),
]

# ─── CONTRADICTORY intents X001-X050 ──────────────────────────────────────────
# Prompt claims HA / fault-tolerance but then explicitly constrains to
# 1 replica / no redundancy / minimal resources. This tests whether the
# MAS planner resolves the contradiction (chooses safety) vs blindly follows.
# structured_features: ha_required=True (the *intended* final state), but
# the contradiction is visible in the prompt text.

CONTRADICTIONS = [
    # (id, ue, thr, slices, scenario, contradiction_phrase, extra_context)
    ("X001", 50, 0.5, [1], "fault-HA", "but keep it to a single replica — I don't want to waste resources", "eMBB, 50 users"),
    ("X002", 100, 1.0, [1], "fault-HA", "with only one instance of each VNF to save costs", "eMBB, 100 users"),
    ("X003", 200, 1.5, [1], "fault-HA", "but set replicas=1 across the board", "eMBB, office building"),
    ("X004", 300, 2.0, [2], "fault-HA", "no standby pods though — just one primary per component", "URLLC, factory"),
    ("X005", 500, 3.0, [1], "fault-HA", "though replica count should stay at 1 to minimise cluster footprint", "eMBB, 500 users"),
    ("X006", 150, 1.2, [2], "fault-HA", "allocate the absolute minimum — one replica per VNF", "URLLC, robots"),
    ("X007", 80, 0.8, [1], "fault-HA", "but I only have budget for a single pod per service", "eMBB, small office"),
    ("X008", 400, 2.5, [1,2], "fault-HA", "keep replicas=1 to conserve node resources", "eMBB+URLLC, mixed"),
    ("X009", 1000, 5.0, [1], "fault-HA", "but no redundancy — single instance only", "eMBB, 1000 UEs"),
    ("X010", 60, 0.6, [2], "fault-HA", "do not add any standby replicas", "URLLC, 60 devices"),
    ("X011", 250, 1.5, [1], "fault-HA", "use replicas=1, I'll handle failover manually", "eMBB, 250 users"),
    ("X012", 700, 4.0, [1,2], "fault-HA", "but limit each VNF to a single instance", "eMBB+URLLC, 700 UEs"),
    ("X013", 120, 1.1, [3], "fault-HA", "though only one pod per VNF is acceptable", "mMTC, IoT"),
    ("X014", 600, 3.5, [1], "fault-HA", "replicas must stay at 1 to fit the budget", "eMBB, 600 users"),
    ("X015", 90, 0.9, [2], "fault-HA", "no pod redundancy please", "URLLC, 90 devices"),
    ("X016", 2000, 8.0, [1], "fault-HA", "single replica across all VNFs", "eMBB, large campus"),
    ("X017", 350, 2.0, [1,2,3], "fault-HA", "but one instance of each component is enough", "tri-slice"),
    ("X018", 500, 3.0, [2], "fault-HA", "keep it lean: replicas=1", "URLLC, 500 devices"),
    ("X019", 100, 1.0, [1], "fault-HA", "though I explicitly don't want HA pods or standby instances", "eMBB, 100 users"),
    ("X020", 800, 4.5, [1], "fault-HA", "no redundant replicas — single pod only", "eMBB, enterprise"),
    ("X021", 50, 0.5, [1], "fault-HA", "with the fewest possible pods — one per VNF", "eMBB, minimal"),
    ("X022", 300, 2.0, [2], "fault-HA", "but limit replica count to exactly 1", "URLLC, 300 robots"),
    ("X023", 1500, 6.0, [1], "fault-HA", "single instance only, I'll add HA later", "eMBB, 1500 users"),
    ("X024", 200, 1.5, [1,2], "fault-HA", "just one replica per service — no standby", "eMBB+URLLC, hospital"),
    ("X025", 75, 0.7, [3], "fault-HA", "one pod per VNF is the hard limit", "mMTC, sensors"),
    ("X026", 450, 2.8, [1], "fault-HA", "replicas=1 is mandatory due to node constraints", "eMBB, 450 users"),
    ("X027", 950, 5.0, [1,2], "fault-HA", "no failover pods — just primaries", "eMBB+URLLC, 950 UEs"),
    ("X028", 130, 1.3, [2], "fault-HA", "keep it to one instance of everything", "URLLC, 130 devices"),
    ("X029", 3000, 12.0, [1], "fault-HA", "but replicas must be 1 across all VNFs", "eMBB, large scale"),
    ("X030", 220, 1.8, [1,3], "fault-HA", "single instance per VNF, no redundancy", "eMBB+mMTC, mixed"),
    # Second batch: contradictory resource claims (ask for huge throughput but tiny UE count)
    ("X031", 10, 0.5, [1], "steady", "sized for 10 devices but I need 50 Gbps throughput for future growth", "eMBB, future-proof"),
    ("X032", 5, 0.3, [2], "steady", "just 5 URLLC devices but provision 100 Gbps capacity", "URLLC, over-provision"),
    ("X033", 20, 0.2, [1], "steady", "20 users but I want 200 Gbps aggregate pipe", "eMBB, over-spec"),
    ("X034", 15, 0.1, [3], "steady", "15 IoT sensors needing 20 Gbps throughput", "mMTC, contradiction"),
    ("X035", 8, 0.05, [2], "steady", "8 URLLC devices but system must handle 50 Gbps", "URLLC, tiny+huge"),
    # Third batch: contradictory latency (ask for mMTC but <1ms latency)
    ("X036", 500, 1.0, [3], "steady", "all on mMTC slice but with sub-millisecond latency guarantee", "mMTC+low-lat"),
    ("X037", 2000, 2.0, [3], "steady", "mMTC sensor mesh but requiring URLLC-grade <1ms response", "mMTC+URLLC mix"),
    ("X038", 1000, 0.5, [3], "steady", "IoT devices on mMTC but demanding real-time 0.5ms latency", "mMTC+realtime"),
    # Fourth batch: autoscale but static (claim sinusoidal but say 'fixed resources')
    ("X039", 500, 2.0, [1], "sinusoidal-autoscale", "but keep resource allocation completely fixed — no HPA", "eMBB, anti-autoscale"),
    ("X040", 1000, 4.0, [1], "sinusoidal-autoscale", "though no autoscaling — hard-coded resource limits only", "eMBB, no HPA"),
    ("X041", 2000, 8.0, [1,2], "sinusoidal-autoscale", "autoscale is not allowed — static allocation mandatory", "multi-slice, no HPA"),
    # Fifth batch: HA + 1 replica on multi-slice (most painful contradiction)
    ("X042", 500, 2.0, [1,2], "fault-HA", "carrier-grade HA but only one instance per VNF across both slices", "eMBB+URLLC HA+1rep"),
    ("X043", 1000, 4.0, [1,2,3], "fault-HA", "five-nines availability but replicas=1 hard limit", "tri-slice HA+1rep"),
    ("X044", 300, 1.5, [2], "fault-HA", "zero-failover tolerance with single-pod deployment only", "URLLC HA+1rep"),
    ("X045", 2000, 8.0, [1], "fault-HA", "non-stop operation but pod count locked at 1 per component", "eMBB HA+1rep"),
    ("X046", 800, 3.5, [1,2], "fault-HA", "blast-zone survivability but no standby replicas allowed", "eMBB+URLLC HA+1rep"),
    ("X047", 400, 2.0, [1], "fault-HA", "geo-resilient but deploy exactly one replica of each VNF", "eMBB HA+1rep"),
    ("X048", 150, 1.2, [2], "fault-HA", "mission-critical HA but I explicitly forbid replica > 1", "URLLC HA+1rep"),
    ("X049", 600, 3.0, [1,2,3], "fault-HA", "carrier-grade resilience with replicas capped at 1", "tri-slice HA+1rep"),
    ("X050", 250, 1.5, [1,2], "fault-HA", "always-on resilience guarantee but single-instance only", "eMBB+URLLC HA+1rep"),
]

def make_contradictory_prompt(spec):
    xid, ue, thr, slices, scenario, contradiction, context = spec
    slice_labels = "/".join(SLICE_NAMES[s] for s in slices)

    if scenario == "fault-HA":
        base = (f"Deploy a high-availability, fault-tolerant 5G core for {ue} UEs "
                f"with {thr} Gbps throughput on {slice_labels} slice(s), "
                f"{contradiction}.")
    elif scenario == "sinusoidal-autoscale":
        base = (f"Deploy a 5G core for {ue} UEs on {slice_labels} slice(s) with "
                f"diurnal autoscaling, {thr} Gbps, {contradiction}.")
    elif scenario in ("steady", "bursty-headroom"):
        base = (f"Deploy 5G for {ue} UEs ({context}), {thr} Gbps aggregate, "
                f"{contradiction}.")
    else:
        base = (f"Deploy 5G for {ue} UEs, {thr} Gbps, {slice_labels}, "
                f"{contradiction}.")
    return base

# ─── BUILD JSON ───────────────────────────────────────────────────────────────

def build_intent(spec, category):
    sid, prompt, complexity, ue, thr, sess, subs, slices, ha, peak, auto, scenario = spec
    ub = ubnd(ue)
    expected = ev(slices, ha)
    return {
        "id":              sid,
        "category":        category,
        "prompt":          prompt,
        "complexity":      complexity,
        "ue_band":         ub,
        "scenario":        scenario,
        "expected_vnfs":   expected,
        "expected_slice_types": sorted(set(slices)),
        "min_vnf_count":   4 if complexity == "simple" else 7,
        "max_vnf_count":   8 if complexity == "simple" else 10,
        "structured_features": sf(ue, thr, sess, subs, slices, ha, peak, auto),
    }

def build_incomplete(spec):
    sid, prompt, complexity, ue_oracle, thr, sess, subs, slices, ha, peak, auto, scenario = spec
    ub = ubnd(ue_oracle)
    expected = ev(slices, ha)
    feat = sf(ue_oracle, thr, sess, subs, slices, ha, peak, auto)
    feat["ue_count"] = None  # oracle value hidden from prompt; planner must infer
    return {
        "id":              sid,
        "category":        "incomplete",
        "prompt":          prompt,
        "complexity":      complexity,
        "ue_band":         ub,
        "scenario":        scenario,
        "expected_vnfs":   expected,
        "expected_slice_types": sorted(set(slices)),
        "min_vnf_count":   4 if complexity == "simple" else 7,
        "max_vnf_count":   8 if complexity == "simple" else 10,
        "structured_features": feat,
        "missing_fields":  ["ue_count"],
    }

def build_contradictory(spec):
    xid, ue, thr, slices, scenario, contradiction, context = spec
    prompt = make_contradictory_prompt(spec)
    complexity = "simple" if ue <= 100 else ("medium" if ue <= 1000 else "complex")
    ub = ubnd(ue)
    expected = ev(slices, ha=True)
    sess = ue; subs = ue
    if slices == [3]:
        sess = max(1, ue // 5); subs = ue
    ha = True
    auto = scenario == "sinusoidal-autoscale"
    peak = 1.0
    feat = sf(ue, thr, sess, subs, slices, ha, peak, auto)
    return {
        "id":              xid,
        "category":        "contradictory",
        "prompt":          prompt,
        "complexity":      complexity,
        "ue_band":         ub,
        "scenario":        scenario,
        "expected_vnfs":   expected,
        "expected_slice_types": sorted(set(slices)),
        "min_vnf_count":   4 if complexity == "simple" else 7,
        "max_vnf_count":   10,
        "structured_features": feat,
        "contradiction_note": contradiction,
    }

def main():
    prompts = []

    # Perfect
    for spec in PERFECT_SPECS:
        prompts.append(build_intent(spec, "perfect"))

    # Ambiguous
    for spec in AMBIGUOUS_SPECS:
        prompts.append(build_intent(spec, "ambiguous"))

    # Incomplete
    for spec in INCOMPLETE_RAW:
        prompts.append(build_incomplete(spec))

    # Contradictory
    for spec in CONTRADICTIONS:
        prompts.append(build_contradictory(spec))

    assert len(prompts) == 300, f"Expected 300, got {len(prompts)}"
    categories = {}
    for p in prompts:
        categories[p["category"]] = categories.get(p["category"], 0) + 1
    print("Category counts:", categories)

    dataset = {
        "experiment": "ambiguity_robustness",
        "version": "3.0",
        "description": (
            "300-intent external-validity dataset. "
            "P001-P100: perfect (clear, unambiguous). "
            "A001-A100: ambiguous (same oracle features, non-standard vocabulary). "
            "I001-I050: incomplete (ue_count missing from prompt). "
            "X001-X050: contradictory (HA requested but single-replica constraint). "
            "Run with --systems mas,b4r to generate the Accuracy-under-Ambiguity graph."
        ),
        "feature_schema": {
            "ue_count": "integer UE count (None for incomplete intents — planner must infer)",
            "throughput_gbps": "aggregate throughput in Gbps",
            "session_count": "concurrent PDU sessions",
            "subscriber_count": "persistent subscribers",
            "slices": "list of {sst} dicts",
            "ha_required": "boolean",
            "peak_multiplier": "burst ratio",
            "autoscale_required": "boolean",
        },
        "categories": {
            "perfect":       "Unambiguous, all parameters explicit. Baseline accuracy.",
            "ambiguous":     "Non-standard vocabulary (synonyms for throughput/latency/devices). Same oracle features.",
            "incomplete":    "UE count omitted from prompt. Oracle ue_count set; planner must infer or request clarification.",
            "contradictory": "HA requested but single-replica constraint imposed. Correct resolution: honour HA (safety > cost).",
        },
        "prompts": prompts,
    }

    OUT.write_text(json.dumps(dataset, indent=2))
    print(f"Written {len(prompts)} intents to {OUT}")

if __name__ == "__main__":
    main()
