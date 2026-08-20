"""llm-check command-line interface."""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

from .config import (
    BenchConfig,
    ModelConfig,
    Prompt,
    has_api_key,
    load_config,
    load_env,
    load_prompts,
    required_env_var,
)
from .reporter import render_per_model_dashboard, render_root_dashboard
from .runner import run_matrix


def _workspace_root() -> Path:
    # Run from the current working dir. The bench is workspace-scoped, not user-scoped.
    return Path.cwd()


def _load_inputs(workspace: Path) -> tuple[BenchConfig, list[Prompt]]:
    load_env(workspace)
    cfg_path = workspace / "config" / "models.yaml"
    if not cfg_path.exists():
        example = workspace / "config" / "models.example.yaml"
        hint = (
            f"\nCopy the example to get started: cp {example} {cfg_path}"
            if example.exists()
            else ""
        )
        raise SystemExit(f"Missing config: {cfg_path}{hint}")
    prompts_dir = workspace / "prompts"
    if not prompts_dir.exists():
        raise SystemExit(f"Missing prompts directory: {prompts_dir}")
    return load_config(cfg_path), load_prompts(prompts_dir)


def _filter_models(models: list[ModelConfig], wanted: list[str] | None) -> list[ModelConfig]:
    enabled = [m for m in models if m.enabled]
    if not wanted:
        return enabled
    by_id = {m.id: m for m in models}
    out: list[ModelConfig] = []
    for w in wanted:
        if w not in by_id:
            raise SystemExit(f"Unknown model id: {w}")
        m = by_id[w]
        m.enabled = True
        out.append(m)
    return out


def _filter_prompts(prompts: list[Prompt], wanted: list[str] | None) -> list[Prompt]:
    if not wanted:
        return prompts
    by_id = {p.id: p for p in prompts}
    out: list[Prompt] = []
    for w in wanted:
        if w not in by_id:
            raise SystemExit(f"Unknown prompt id: {w}")
        out.append(by_id[w])
    return out


def cmd_run(args: argparse.Namespace) -> int:
    console = Console()
    workspace = _workspace_root()
    config, prompts = _load_inputs(workspace)
    models = _filter_models(config.models, args.model)
    prompts = _filter_prompts(prompts, args.prompt)
    if not prompts:
        console.print("[red]No prompts found.[/red]")
        return 1
    runs_root = workspace / "runs"
    runs_root.mkdir(exist_ok=True)
    per_model_run_dirs = asyncio.run(
        run_matrix(
            config=config,
            prompts=prompts,
            models=models,
            runs_root=runs_root,
            console=console,
            dry_run=args.dry_run,
        )
    )
    if not per_model_run_dirs:
        # run_matrix aborted (e.g. no usable models).
        return 1
    # Per-model dashboards
    for d in per_model_run_dirs:
        if (d / "results.json").exists():
            render_per_model_dashboard(d)
    # Aggregate (root) dashboard scans ALL per-model runs on disk and shows the
    # latest result per model side by side.
    root_dash = render_root_dashboard(runs_root)
    console.print(f"\n[bold green]Done.[/bold green] Overview: file://{root_dash.resolve()}")
    for d in per_model_run_dirs:
        per = d / "index.html"
        if per.exists():
            console.print(f"  - {d.name}: file://{per.resolve()}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    """Re-render dashboards from existing results.json (no LLM calls).

    - If `target` is a per-model run dir, re-render that dashboard.
    - If `target` is the runs/ root, re-render the aggregate dashboard.
    - With no argument, re-render the aggregate dashboard for ./runs.
    """
    console = Console()
    target = Path(args.run_dir).resolve() if args.run_dir else (_workspace_root() / "runs").resolve()

    if not target.exists():
        console.print(f"[red]No such path: {target}[/red]")
        return 1

    # Per-model run dir?
    if (target / "results.json").exists() and (target / "outputs").exists():
        dash = render_per_model_dashboard(target)
        console.print(f"Re-rendered per-model: file://{dash}")
        # Also refresh the aggregate so the root view stays in sync.
        root = render_root_dashboard(target.parent)
        console.print(f"Re-rendered aggregate: file://{root}")
        return 0

    # Treat as runs/ root.
    root = render_root_dashboard(target)
    console.print(f"Re-rendered aggregate: file://{root}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    console = Console()
    workspace = _workspace_root()
    config, prompts = _load_inputs(workspace)

    p_table = Table(title="Prompts", show_header=True, header_style="bold")
    p_table.add_column("id")
    p_table.add_column("file")
    p_table.add_column("chars", justify="right")
    for p in prompts:
        p_table.add_row(p.id, str(p.path.relative_to(workspace)), str(len(p.body)))
    console.print(p_table)

    m_table = Table(title="Models", show_header=True, header_style="bold")
    m_table.add_column("id")
    m_table.add_column("label")
    m_table.add_column("provider")
    m_table.add_column("kind")
    m_table.add_column("endpoint")
    m_table.add_column("model_name")
    m_table.add_column("enabled")
    m_table.add_column("key/ready")
    for m in config.models:
        kind = "dry-run" if m.is_dry_run else ("local" if m.is_local else "cloud")
        endpoint = m.api_base or "-"
        if m.is_dry_run:
            ready = "[green]yes[/green]"
        elif m.is_local:
            ready = "[green]yes[/green]"
        else:
            ready = "[green]yes[/green]" if has_api_key(m) else f"[red]no ({required_env_var(m)})[/red]"
        m_table.add_row(
            m.id,
            m.label,
            m.provider,
            kind,
            endpoint,
            m.model_name or "[dim]auto[/dim]",
            "[green]on[/green]" if m.enabled else "[dim]off[/dim]",
            ready,
        )
    console.print(m_table)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="llm-check", description="Local-first LLM test bench.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Run the prompt x model matrix.")
    p_run.add_argument("-p", "--prompt", action="append", help="Prompt id (repeatable). Default: all.")
    p_run.add_argument("-m", "--model", action="append", help="Model id (repeatable). Default: enabled models.")
    p_run.add_argument(
        "--dry-run",
        action="store_true",
        help="Use the built-in canned provider; no network or LLM needed.",
    )
    p_run.set_defaults(func=cmd_run)

    p_val = sub.add_parser(
        "validate",
        help="Re-render dashboards (per-model or aggregate) without calling any LLM.",
    )
    p_val.add_argument(
        "run_dir",
        nargs="?",
        default=None,
        help="Path to a runs/<model>__<device> directory, or runs/ itself. Defaults to ./runs.",
    )
    p_val.set_defaults(func=cmd_validate)

    p_list = sub.add_parser("list", help="List prompts and models.")
    p_list.set_defaults(func=cmd_list)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
