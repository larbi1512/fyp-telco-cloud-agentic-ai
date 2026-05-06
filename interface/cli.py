"""
Rich CLI — Terminal interface for the 5G Orchestrator.

Provides formatted output for agent results and handles user
interaction at HITL (Human-in-the-Loop) checkpoints.
"""

from __future__ import annotations

import logging
from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

console = Console()
logger = logging.getLogger(__name__)

BANNER = r"""
[bold cyan]
  ╔═══════════════════════════════════════════════════════════╗
  ║           5G Network Orchestrator — MAS Pipeline          ║
  ║              Phases 2–4: Plan → Deploy → Verify           ║
  ╚═══════════════════════════════════════════════════════════╝
[/bold cyan]
"""


def print_welcome(log_file: str | None = None) -> None:
    """Print the welcome banner and log file path."""
    console.print(BANNER)
    if log_file:
        console.print(f"  [dim]Log file: {log_file}[/dim]\n")


def capture_intent() -> str:
    """Prompt the user for their network deployment intent."""
    console.print(
        Panel(
            "[bold]Describe your 5G network deployment requirements:[/bold]\n"
            "[dim]Example: Deploy a 5G core for 500 UEs with 2 Gbps throughput and eMBB slicing.[/dim]",
            title="[bold green]User Intent[/bold green]",
            border_style="green",
        )
    )
    intent = console.input("[bold green]>>> [/bold green]")
    logger.info("User intent captured: %s", intent)
    return intent.strip()


def display_agent_start(agent_name: str) -> None:
    """Show a status line when an agent starts executing."""
    console.print(f"\n  [bold yellow]⏳ Running {agent_name}...[/bold yellow]")


def display_agent_done(agent_name: str) -> None:
    """Show a status line when an agent finishes."""
    console.print(f"  [bold green]✅ {agent_name} complete[/bold green]")


def display_topology(topology: dict[str, Any]) -> None:
    """Render the topology blueprint as a Rich table."""
    if not topology:
        return

    console.print(
        Panel(
            f"[bold]Topology ID:[/bold] {topology.get('topology_id', 'N/A')}",
            title="[bold cyan]Network Topology[/bold cyan]",
            border_style="cyan",
        )
    )

    # VNF table
    table = Table(title="VNFs", box=box.ROUNDED, show_lines=True)
    table.add_column("Name", style="bold")
    table.add_column("Type")
    table.add_column("Replicas", justify="center")
    table.add_column("Interfaces")
    table.add_column("Slices")

    for vnf in topology.get("vnfs", []):
        ifaces = ", ".join(
            i.get("name", "?") for i in vnf.get("interfaces", [])
        )
        slices = ", ".join(
            f"SST={s.get('sst')}" for s in vnf.get("supported_slices", [])
        )
        table.add_row(
            vnf.get("name", "?"),
            vnf.get("type", "?"),
            str(vnf.get("replicas", 1)),
            ifaces or "—",
            slices or "—",
        )

    console.print(table)

    # Connectivity
    conn = topology.get("connectivity", {})
    if conn:
        plmn = conn.get("plmn", {})
        console.print(
            f"  [bold]PLMN:[/bold] MCC={plmn.get('mcc', '?')}, MNC={plmn.get('mnc', '?')}"
        )
        slices = conn.get("slices", [])
        if slices:
            slice_strs = [f"SST={s.get('sst')} SD={s.get('sd', 'N/A')}" for s in slices]
            console.print(f"  [bold]Slices:[/bold] {', '.join(slice_strs)}")
        dnns = conn.get("dnns", [])
        if dnns:
            console.print(f"  [bold]DNNs:[/bold] {', '.join(dnns)}")

    # SLA
    sla = topology.get("sla", {})
    if sla:
        console.print(
            f"  [bold]SLA:[/bold] latency<{sla.get('max_latency_ms', '?')}ms, "
            f"throughput≥{sla.get('min_throughput_gbps', '?')}Gbps"
        )


def display_resource_allocation(alloc: dict[str, Any]) -> None:
    """Render the resource allocation as a Rich table."""
    if not alloc:
        return

    table = Table(title="Resource Allocation", box=box.ROUNDED, show_lines=True)
    table.add_column("VNF", style="bold")
    table.add_column("CPU Req")
    table.add_column("CPU Lim")
    table.add_column("Mem Req")
    table.add_column("Mem Lim")

    for vnf in alloc.get("vnfs", []):
        res = vnf.get("resources", {})
        req = res.get("requests", {})
        lim = res.get("limits", {})
        table.add_row(
            vnf.get("name", "?"),
            req.get("cpu", "—"),
            lim.get("cpu", "—"),
            req.get("memory", "—"),
            lim.get("memory", "—"),
        )

    console.print(table)


def display_validation_report(report: dict[str, Any]) -> None:
    """Render the validation report as a Rich table."""
    if not report:
        return

    decision = report.get("decision", "UNKNOWN")
    style = "bold green" if decision == "GO" else "bold red"

    table = Table(
        title=f"Policy Validation — [{style}]{decision}[/{style}]",
        box=box.ROUNDED,
        show_lines=True,
    )
    table.add_column("Check", style="bold")
    table.add_column("Status")
    table.add_column("Violations", justify="center")
    table.add_column("Details")

    status_icons = {"PASS": "", "WARNING": "", "FAIL": ""}

    for name, data in report.get("policy_checks", {}).items():
        status = data.get("status", "?")
        icon = status_icons.get(status, "❓")
        table.add_row(
            name,
            f"{icon} {status}",
            str(data.get("violations", 0)),
            str(data.get("details", ""))[:50],
        )

    console.print(table)

    # Dry-run
    dry = report.get("dry_run", {})
    if dry:
        ds = dry.get("status", "?")
        console.print(f"  [bold]Dry-run:[/bold] {ds}")

    # Conflicts
    conflicts = report.get("conflicts", [])
    if conflicts:
        console.print("  [bold]Conflicts:[/bold]")
        for c in conflicts:
            console.print(f"    • {c}")

    # Reasons
    reasons = report.get("reasons", [])
    if reasons:
        console.print("  [bold]Reasons:[/bold]")
        for r in reasons:
            console.print(f"    — {r}")


def display_agent_message(message: dict[str, str]) -> None:
    """Render a single agent message as Markdown inside a panel."""
    content = message.get("content", "")
    role = message.get("role", "agent").upper()
    console.print(
        Panel(
            Markdown(content),
            title=f"[bold]{role}[/bold]",
            border_style="blue",
        )
    )


def ask_approval(prompt_text: str = "Approve?") -> bool:
    """Ask the user for yes/no approval at a HITL checkpoint."""
    console.print(
        Panel(
            f"[bold]{prompt_text}[/bold]",
            title="[bold yellow]Approval Required[/bold yellow]",
            border_style="yellow",
        )
    )
    while True:
        choice = console.input("[bold yellow][y/n] >>> [/bold yellow]").strip().lower()
        if choice in ("y", "yes"):
            logger.info("User approved: %s", prompt_text)
            return True
        elif choice in ("n", "no"):
            logger.info("User rejected: %s", prompt_text)
            return False
        console.print("  [dim]Please enter 'y' or 'n'[/dim]")


def display_error(error: str) -> None:
    """Display an error message."""
    console.print(f"\n  [bold red] Error: {error}[/bold red]\n")


def display_deployment_results(results: list[dict[str, Any]]) -> None:
    """Render deployment results as a Rich table."""
    if not results:
        return

    table = Table(title="Deployment Results", box=box.ROUNDED, show_lines=True)
    table.add_column("VNF", style="bold")
    table.add_column("Release")
    table.add_column("Status")
    table.add_column("Message")

    status_styles = {
        "installed": "green",
        "upgraded": "cyan",
        "failed": "red",
        "skipped": "yellow",
    }

    for r in results:
        style = status_styles.get(r.get("status", ""), "white")
        table.add_row(
            r.get("vnf_name", "?"),
            r.get("release_name", "?"),
            f"[{style}]{r.get('status', '?')}[/{style}]",
            str(r.get("message", ""))[:80],
        )

    console.print(table)


def display_final_status(decision: str) -> None:
    """Display the final pipeline outcome."""
    if decision == "GO":
        console.print(
            Panel(
                "[bold green]🚀 Pipeline complete — Deployment executed on remote cluster![/bold green]",
                border_style="green",
            )
        )
    else:
        console.print(
            Panel(
                "[bold red]⛔ Pipeline halted — Deployment not approved.[/bold red]",
                border_style="red",
            )
        )


# ──────────────────── Post-deployment monitoring display ──────────────────── #


def display_metrics_summary(metrics: list[dict[str, Any]]) -> None:
    """Render a compact table of the current monitoring cycle's KPI metrics."""
    if not metrics:
        console.print("  [dim]No metrics collected this cycle.[/dim]")
        return

    table = Table(title="KPI Monitor", box=box.ROUNDED, show_lines=True)
    table.add_column("VNF", style="bold")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    table.add_column("Unit")
    table.add_column("Status")

    status_styles = {
        "normal": "green",
        "warning": "yellow",
        "critical": "red",
    }

    for m in metrics:
        status = m.get("threshold_status", "normal")
        style = status_styles.get(status, "white")
        table.add_row(
            m.get("vnf_name", "?"),
            m.get("metric_name", "?"),
            f"{m.get('value', 0):.1f}",
            m.get("unit", ""),
            f"[{style}]{status}[/{style}]",
        )

    console.print(table)


def display_anomaly_alerts(alerts: list[dict[str, Any]]) -> None:
    """Render anomaly alerts. Shows a green status line when none are detected."""
    if not alerts:
        console.print("  [bold green]No anomalies detected.[/bold green]")
        return

    for alert in alerts:
        confidence = alert.get("confidence", 0.0)
        border = "red" if confidence >= 0.85 else "yellow"
        title_color = "red" if confidence >= 0.85 else "yellow"

        affected = ", ".join(
            f"{m.get('name', '?')} ({m.get('current', '?')} {m.get('unit', '')})"
            for m in alert.get("affected_metrics", [])
        )
        actions = "\n".join(
            f"  • {a}" for a in alert.get("suggested_actions", [])
        )

        body = (
            f"[bold]Type:[/bold]       {alert.get('type', '?')}\n"
            f"[bold]Affected:[/bold]   {affected or '—'}\n"
            f"[bold]Cause:[/bold]      {alert.get('suggested_cause', '—')}\n"
            f"[bold]Actions:[/bold]\n{actions or '  —'}"
        )

        console.print(
            Panel(
                body,
                title=f"[bold {title_color}]ANOMALY ALERT  [confidence: {confidence:.2f}][/bold {title_color}]",
                border_style=border,
            )
        )


def display_remediation_plan(plan: dict[str, Any] | None) -> None:
    """Render a remediation plan. Shows a neutral line when no plan is needed."""
    if not plan:
        console.print("  [dim]No remediation needed this cycle.[/dim]")
        return

    confidence = plan.get("confidence", 0.0)
    header = (
        f"[bold]Plan ID:[/bold]    {plan.get('plan_id', '?')}\n"
        f"[bold]Triggered by:[/bold] {plan.get('triggered_by', '?')}\n"
        f"[bold]Confidence:[/bold]  {confidence:.2f}\n"
        f"[bold]Diagnosis:[/bold]  {plan.get('diagnosis', '—')}\n"
    )

    actions = plan.get("recommended_actions", [])
    if actions:
        table = Table(box=box.SIMPLE, show_header=True, padding=(0, 1))
        table.add_column("#", justify="right", style="dim")
        table.add_column("Type", style="bold")
        table.add_column("Target")
        table.add_column("Action")
        table.add_column("Reason")
        for a in sorted(actions, key=lambda x: int(x.get("order", 99))):
            table.add_row(
                str(a.get("order", "?")),
                a.get("type", "?"),
                a.get("target", "?"),
                a.get("action", "?"),
                str(a.get("reason", ""))[:60],
            )

    from rich.console import Group
    from rich.text import Text
    content: Any = Group(Text.from_markup(header), table) if actions else Text.from_markup(header)

    console.print(
        Panel(
            content,
            title="[bold cyan]Remediation Plan[/bold cyan]",
            border_style="cyan",
        )
    )
