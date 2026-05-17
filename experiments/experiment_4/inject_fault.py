"""
Experiment 4 — Fault Injection CLI.

Injects one of three fault scenarios into the live OAI 5G core deployment
on the remote Kubernetes cluster:

  F1 — CPU spike   : run a stress-ng sidecar pod targeting the same node
                     as the target VNF, creating CPU contention.
  F2 — Pod crash   : delete the target VNF's pod; if --loop is passed the
                     pod is repeatedly deleted to simulate a crash loop.
  F3 — Traffic surge: launch an iperf3 client pod against the UPF service
                     with N parallel streams to drive throughput up.

Each fault returns a structured JSON record with the injection timestamp,
duration, and target so run_scenario.py can correlate it with the
monitoring loop's structured log output.

Usage:
    python inject_fault.py --scenario F1 --target oai-amf --duration 60
    python inject_fault.py --scenario F2 --target oai-upf
    python inject_fault.py --scenario F3 --duration 60 --streams 50
"""
from __future__ import annotations

import argparse
import json
import logging
import shlex
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# Default namespace; can be overridden via CLI.
DEFAULT_NAMESPACE = "oai-5g"


def _kubectl(args: list[str], timeout: int = 60) -> tuple[int, str, str]:
    """Run kubectl with the supplied argv tail. Returns (rc, stdout, stderr)."""
    cmd = ["kubectl", *args]
    logger.debug("$ %s", " ".join(shlex.quote(c) for c in cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return proc.returncode, proc.stdout, proc.stderr


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _find_pod(namespace: str, vnf: str) -> str | None:
    """Return the first running pod whose name starts with the VNF prefix."""
    rc, out, err = _kubectl([
        "get", "pods", "-n", namespace,
        "-o", "jsonpath={.items[?(@.status.phase==\"Running\")].metadata.name}",
    ])
    if rc != 0:
        logger.error("kubectl get pods failed: %s", err.strip())
        return None
    for name in out.split():
        if name.startswith(vnf):
            return name
    return None


def inject_cpu_spike(namespace: str, target: str, duration: int) -> dict:
    """
    F1 — CPU spike. Spawn a stress-ng pod with node-affinity matching the
    target VNF's node so the contention is felt by the target.
    """
    pod = _find_pod(namespace, target)
    if not pod:
        raise RuntimeError(f"could not find a Running pod for VNF={target!r}")

    # Discover the node the target pod is on so the stressor lands there.
    rc, out, err = _kubectl([
        "get", "pod", pod, "-n", namespace,
        "-o", "jsonpath={.spec.nodeName}",
    ])
    if rc != 0:
        raise RuntimeError(f"could not read node for pod {pod}: {err}")
    node = out.strip() or None

    stress_name = f"f1-cpu-stress-{uuid.uuid4().hex[:6]}"
    overrides = {
        "spec": {
            "nodeName": node,
            "tolerations": [{"operator": "Exists"}],
            "containers": [{
                "name": stress_name,
                "image": "polinux/stress",
                "command": ["stress", "--cpu", "4", "--timeout", f"{duration}s"],
            }],
        }
    } if node else {
        "spec": {
            "containers": [{
                "name": stress_name,
                "image": "polinux/stress",
                "command": ["stress", "--cpu", "4", "--timeout", f"{duration}s"],
            }],
        }
    }
    started_at = _now()
    rc, out, err = _kubectl([
        "run", stress_name, "-n", namespace,
        "--restart=Never", "--image=polinux/stress",
        "--overrides", json.dumps(overrides),
        "--", "stress", "--cpu", "4", "--timeout", f"{duration}s",
    ], timeout=30)
    if rc != 0:
        # `kubectl run --overrides` on some versions ignores the image flag;
        # fall back to a simpler form without node pinning.
        logger.warning("stress pod with node pinning failed (%s); retrying simple form", err.strip())
        rc, out, err = _kubectl([
            "run", stress_name, "-n", namespace,
            "--restart=Never", "--image=polinux/stress",
            "--", "stress", "--cpu", "4", "--timeout", f"{duration}s",
        ], timeout=30)
    if rc != 0:
        raise RuntimeError(f"failed to start stress pod: {err}")

    return {
        "scenario": "F1",
        "fault_type": "cpu_spike",
        "target_vnf": target,
        "target_pod": pod,
        "target_node": node,
        "stress_pod": stress_name,
        "duration_s": duration,
        "started_at": started_at,
        "namespace": namespace,
    }


def inject_pod_crash(namespace: str, target: str, loop_count: int = 1) -> dict:
    """
    F2 — Pod crash. Delete the target VNF's pod *loop_count* times with a
    short sleep between deletes to simulate a crash loop. Kubernetes will
    recreate the pod each time, so the controller must observe the crash
    and decide to remediate.
    """
    namespace_args = ["-n", namespace]
    deletions: list[dict] = []
    for i in range(loop_count):
        pod = _find_pod(namespace, target)
        if not pod:
            logger.warning("F2 iter %d: no running pod for %s", i, target)
            break
        ts = _now()
        rc, out, err = _kubectl(["delete", "pod", pod, "--grace-period=0", "--force", *namespace_args])
        deletions.append({"iter": i, "pod": pod, "rc": rc, "ts": ts})
        if loop_count > 1 and i < loop_count - 1:
            time.sleep(20)  # let K8s recreate before next strike

    return {
        "scenario": "F2",
        "fault_type": "pod_crash" if loop_count == 1 else "crash_loop",
        "target_vnf": target,
        "loop_count": loop_count,
        "deletions": deletions,
        "started_at": deletions[0]["ts"] if deletions else _now(),
        "namespace": namespace,
    }


def inject_traffic_surge(namespace: str, duration: int, streams: int) -> dict:
    """
    F3 — Traffic surge. Spawn an iperf3 client pod that runs *streams*
    parallel TCP streams against the UPF service for *duration* seconds.

    Assumes a Service named 'oai-upf' exists in *namespace*. The probe
    runs through the K8s ClusterIP, which targets the UPF data path.
    """
    iperf_name = f"f3-iperf-{uuid.uuid4().hex[:6]}"
    started_at = _now()
    cmd = [
        "run", iperf_name, "-n", namespace,
        "--restart=Never", "--image=networkstatic/iperf3",
        "--", "-c", "oai-upf", "-t", str(duration), "-P", str(streams),
    ]
    rc, out, err = _kubectl(cmd, timeout=30)
    if rc != 0:
        raise RuntimeError(f"failed to start iperf3 pod: {err}")
    return {
        "scenario": "F3",
        "fault_type": "traffic_surge",
        "iperf_pod": iperf_name,
        "duration_s": duration,
        "streams": streams,
        "started_at": started_at,
        "namespace": namespace,
    }


def cleanup(namespace: str, record: dict) -> None:
    """Best-effort removal of injection artifacts (stress / iperf pods)."""
    pods_to_delete: list[str] = []
    if record.get("stress_pod"):
        pods_to_delete.append(record["stress_pod"])
    if record.get("iperf_pod"):
        pods_to_delete.append(record["iperf_pod"])
    for p in pods_to_delete:
        rc, _, err = _kubectl(
            ["delete", "pod", p, "-n", namespace, "--ignore-not-found", "--grace-period=0", "--force"],
            timeout=30,
        )
        if rc != 0:
            logger.warning("cleanup of %s failed: %s", p, err.strip())


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--scenario", choices=["F1", "F2", "F3"], required=True)
    p.add_argument("--target", default="oai-amf",
                   help="Target VNF prefix for F1 / F2 (e.g. oai-amf, oai-upf).")
    p.add_argument("--duration", type=int, default=60,
                   help="Fault duration in seconds (F1 / F3).")
    p.add_argument("--streams", type=int, default=50,
                   help="Parallel TCP streams for F3 traffic surge.")
    p.add_argument("--loop", type=int, default=1,
                   help="Number of pod deletions for F2 (>1 = crash loop).")
    p.add_argument("--namespace", default=DEFAULT_NAMESPACE)
    p.add_argument("--no-cleanup", action="store_true",
                   help="Skip post-fault cleanup (debug only).")
    p.add_argument("--output", help="Write the injection record to this JSON file.")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    try:
        if args.scenario == "F1":
            record = inject_cpu_spike(args.namespace, args.target, args.duration)
        elif args.scenario == "F2":
            record = inject_pod_crash(args.namespace, args.target, args.loop)
        else:  # F3
            record = inject_traffic_surge(args.namespace, args.duration, args.streams)
    except Exception as exc:
        logger.error("injection failed: %s", exc)
        return 2

    record["finished_at"] = _now()

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(record, indent=2))
        logger.info("injection record written to %s", args.output)
    print(json.dumps(record, indent=2))

    if not args.no_cleanup:
        # Wait the fault duration so the stressor / iperf finishes, then clean up.
        if args.scenario in ("F1", "F3"):
            logger.info("waiting %ds for fault to complete before cleanup", args.duration)
            time.sleep(args.duration + 5)
        cleanup(args.namespace, record)

    return 0


if __name__ == "__main__":
    sys.exit(main())
