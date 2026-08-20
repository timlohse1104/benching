"""Run the prompt x model matrix and produce a result set."""
from __future__ import annotations

import asyncio
import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from .config import BenchConfig, ModelConfig, Prompt, has_api_key, required_env_var
from .providers import complete
from .validators import extract_html, headless_validate, parse_validate


@dataclass
class CellResult:
    prompt_id: str
    model_id: str
    model_label: str
    provider: str
    is_local: bool
    is_dry_run: bool
    kind: str | None = None
    html_file: str | None = None
    raw_file: str | None = None
    thumbnail: str | None = None
    latency_ms: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cost_usd: float | None = None
    status: str = "pending"   # ok | warnings | broken | failed | skipped
    looks_like_html: bool = False
    validation: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


def _probe_served_models(api_base: str, timeout_s: float = 2.0) -> tuple[bool, list[str], str | None]:
    """GET {api_base}/models. Returns (reachable, [served names], error_message).

    `api_base` is expected to already include the OpenAI-compatible path prefix
    (e.g. http://localhost:8080/v1).
    """
    url = api_base.rstrip("/") + "/models"
    try:
        resp = httpx.get(url, timeout=timeout_s)
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - we surface the message
        return False, [], f"{type(exc).__name__}: {exc}"
    try:
        payload = resp.json()
    except Exception as exc:
        return False, [], f"invalid JSON from {url}: {exc}"
    names: list[str] = []
    for item in payload.get("data") or []:
        name = item.get("id") if isinstance(item, dict) else None
        if name:
            names.append(name)
    return True, names, None


def _preflight(console: Console, models: list[ModelConfig]) -> list[ModelConfig]:
    """Check reachability / API keys / served model names. Return usable models."""
    table = Table(title="Preflight", show_header=True, header_style="bold")
    table.add_column("model")
    table.add_column("kind")
    table.add_column("endpoint")
    table.add_column("served / requested")
    table.add_column("status")

    # Probe each unique local OpenAI-compatible api_base once
    probe_cache: dict[str, tuple[bool, list[str], str | None]] = {}
    for m in models:
        if m.is_openai_compatible_local and m.api_base and m.api_base not in probe_cache:
            probe_cache[m.api_base] = _probe_served_models(m.api_base)

    usable: list[ModelConfig] = []
    for m in models:
        kind = "local" if m.is_local else "cloud"
        endpoint = m.api_base or m.provider.split("/", 1)[0]

        if m.is_dry_run:
            table.add_row(m.id, "dry-run", "in-process", "-", "[green]OK[/green]")
            usable.append(m)
            continue

        if not m.is_local and not has_api_key(m):
            var = required_env_var(m) or "?"
            table.add_row(m.id, kind, endpoint, "-", f"[red]no key ({var})[/red] -> skipped")
            continue

        if m.is_openai_compatible_local and m.api_base:
            reachable, served, err = probe_cache.get(m.api_base, (False, [], "no probe"))
            served_summary = ", ".join(served) if served else "[dim]none[/dim]"
            if not reachable:
                table.add_row(
                    m.id,
                    kind,
                    endpoint,
                    served_summary,
                    f"[red]unreachable[/red] ({err}) -> skipped",
                )
                continue
            if m.model_name and m.model_name not in served:
                table.add_row(
                    m.id,
                    kind,
                    endpoint,
                    f"{served_summary}  /  [red]want: {m.model_name}[/red]",
                    "[red]model not served[/red] -> skipped",
                )
                continue
            shown_name = m.model_name or (served[0] if served else "?")
            table.add_row(
                m.id,
                kind,
                endpoint,
                f"{served_summary}  /  [green]use: {shown_name}[/green]",
                "[green]OK[/green]",
            )
            # If the user didn't pin a model_name and the server reports one, adopt it
            # so it is recorded in results.json.
            if not m.model_name and served:
                m.model_name = served[0]
            usable.append(m)
            continue

        # Cloud or other local providers (ollama/<name>): no probe.
        table.add_row(m.id, kind, endpoint, "-", "[green]OK[/green]")
        usable.append(m)

    console.print(table)
    return usable


async def _run_one(
    semaphore_global: asyncio.Semaphore,
    semaphore_model: asyncio.Semaphore,
    semaphore_endpoint: asyncio.Semaphore,
    config: BenchConfig,
    model: ModelConfig,
    prompt: Prompt,
    outputs_dir: Path,
    thumbs_dir: Path,
) -> CellResult:
    cell = CellResult(
        prompt_id=prompt.id,
        model_id=model.id,
        model_label=model.label,
        provider=model.provider,
        is_local=model.is_local,
        is_dry_run=model.is_dry_run,
        kind=model.kind,
    )

    async with semaphore_global, semaphore_endpoint, semaphore_model:
        completion = await complete(model, config, prompt.id, prompt.body)

    cell.latency_ms = completion.latency_ms
    cell.prompt_tokens = completion.prompt_tokens
    cell.completion_tokens = completion.completion_tokens
    cell.total_tokens = completion.total_tokens
    cell.cost_usd = completion.cost_usd

    if completion.error:
        cell.status = "failed"
        cell.error = completion.error
        return cell

    html, looks_html = extract_html(completion.text)
    cell.looks_like_html = looks_html

    base_name = f"{prompt.id}__{model.id}"
    html_path = outputs_dir / f"{base_name}.html"
    html_path.write_text(html or completion.text, encoding="utf-8")
    # Store paths relative to the run dir so the dashboard JS can use them
    # verbatim as URLs (works for per-model and aggregate dashboards alike).
    cell.html_file = f"outputs/{html_path.name}"

    if not looks_html:
        # Still save the raw text for inspection
        raw_path = outputs_dir / f"{base_name}.raw.txt"
        raw_path.write_text(completion.text, encoding="utf-8")
        cell.raw_file = f"outputs/{raw_path.name}"

    # Parser-based validation
    parse = parse_validate(html or completion.text)

    # Headless validation + thumbnail
    thumb_path = thumbs_dir / f"{base_name}.png"
    runtime_issues, thumb_name = await headless_validate(html_path, thumb_path)
    parse.runtime_issues = runtime_issues
    parse.thumbnail_path = f"thumbnails/{thumb_name}" if thumb_name else None
    cell.thumbnail = f"thumbnails/{thumb_name}" if thumb_name else None
    cell.validation = parse.to_dict()
    cell.status = parse.status
    return cell


def _slug(value: str) -> str:
    """Filesystem-safe slug: keep [A-Za-z0-9._-], collapse everything else to '-'."""
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    return s.strip("-") or "x"


def _run_dir_name(model: ModelConfig) -> str:
    """Stable per-model+device run-dir name: `{model_id}__{kind}` (or `{model_id}`).

    `kind` is the free-form device/host label. Re-running the same model+device
    combination reuses (and overwrites) the same directory.
    """
    base = _slug(model.id)
    if model.kind:
        return f"{base}__{_slug(model.kind)}"
    return base


async def run_matrix(
    config: BenchConfig,
    prompts: list[Prompt],
    models: list[ModelConfig],
    runs_root: Path,
    console: Console,
    dry_run: bool,
) -> list[Path]:
    """Run prompts x models. Each model gets its OWN run dir under runs/.

    Returns the list of created per-model run dirs.
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    runs_root.mkdir(parents=True, exist_ok=True)

    # Enable dry-run model when requested; ignore all other models in pure dry-run.
    if dry_run:
        dry_models = [m for m in config.models if m.is_dry_run]
        if not dry_models:
            # Synthesize one on the fly
            dry_models = [
                ModelConfig(
                    id="dry-run",
                    provider="dryrun/canned",
                    label="Dry-run (canned HTML)",
                )
            ]
        # Force-enable
        for m in dry_models:
            m.enabled = True
        models = [m for m in dry_models if m.enabled]
    else:
        # Skip dry-run models in normal runs
        models = [m for m in models if not m.is_dry_run]

    usable = _preflight(console, models)
    if not usable:
        console.print("[red]No usable models. Aborting.[/red]")
        return []

    semaphore_global = asyncio.Semaphore(config.defaults.concurrency)
    per_model_sem: dict[str, asyncio.Semaphore] = {
        m.id: asyncio.Semaphore(m.concurrency or config.defaults.concurrency) for m in usable
    }
    # Per-endpoint semaphore. A single local OpenAI-compatible server (llama.cpp,
    # LM Studio, ...) serves only the model it has loaded right now, so we must
    # serialize all requests targeting the same `api_base`. Different api_base
    # values are independent. Non-local models use a noop semaphore.
    _NOOP_KEY = object()
    endpoint_sem: dict[Any, asyncio.Semaphore] = {}

    def _endpoint_key(m: ModelConfig) -> Any:
        if m.api_base:
            return ("api_base", m.api_base)
        return _NOOP_KEY

    for m in usable:
        key = _endpoint_key(m)
        if key not in endpoint_sem:
            # 1 for local OpenAI-compatible servers, large for noop.
            endpoint_sem[key] = asyncio.Semaphore(1 if m.api_base else 1024)

    # One run-dir PER model+device combination. All cells of that model land in
    # runs/<model_id>__<kind>/{outputs,thumbnails}. Re-running the same
    # combination overwrites the directory (wipe first so a smaller prompt set
    # leaves no stale outputs behind).
    model_dirs: dict[str, Path] = {}
    for m in usable:
        d = runs_root / _run_dir_name(m)
        if d.exists():
            shutil.rmtree(d)
        (d / "outputs").mkdir(parents=True, exist_ok=True)
        (d / "thumbnails").mkdir(parents=True, exist_ok=True)
        model_dirs[m.id] = d

    tasks = [
        _run_one(
            semaphore_global,
            per_model_sem[m.id],
            endpoint_sem[_endpoint_key(m)],
            config,
            m,
            p,
            model_dirs[m.id] / "outputs",
            model_dirs[m.id] / "thumbnails",
        )
        for p in prompts
        for m in usable
    ]
    total = len(tasks)

    results: list[CellResult] = []
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Running", total=total)
        for fut in asyncio.as_completed(tasks):
            cell = await fut
            results.append(cell)
            progress.update(task, advance=1)

    # Write one results.json per model
    by_model: dict[str, list[CellResult]] = {m.id: [] for m in usable}
    for r in results:
        by_model.setdefault(r.model_id, []).append(r)

    written: list[Path] = []
    for m in usable:
        payload = _build_payload(
            timestamp=timestamp,
            config=config,
            prompts=prompts,
            models=[m],
            results=by_model.get(m.id, []),
            dry_run=dry_run,
        )
        d = model_dirs[m.id]
        (d / "results.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
        written.append(d)

    return written


def _build_payload(
    timestamp: str,
    config: BenchConfig,
    prompts: list[Prompt],
    models: list[ModelConfig],
    results: list[CellResult],
    dry_run: bool,
) -> dict[str, Any]:
    by_pair: dict[tuple[str, str], CellResult] = {(r.prompt_id, r.model_id): r for r in results}
    cells = []
    for p in prompts:
        for m in models:
            r = by_pair.get((p.id, m.id))
            if r is None:
                continue
            cells.append(
                {
                    "prompt_id": r.prompt_id,
                    "model_id": r.model_id,
                    "model_label": r.model_label,
                    "provider": r.provider,
                    "is_local": r.is_local,
                    "is_dry_run": r.is_dry_run,
                    "kind": r.kind,
                    "status": r.status,
                    "html_file": r.html_file,
                    "raw_file": r.raw_file,
                    "thumbnail": r.thumbnail,
                    "latency_ms": r.latency_ms,
                    "prompt_tokens": r.prompt_tokens,
                    "completion_tokens": r.completion_tokens,
                    "total_tokens": r.total_tokens,
                    "cost_usd": r.cost_usd,
                    "looks_like_html": r.looks_like_html,
                    "validation": r.validation,
                    "error": r.error,
                }
            )

    total_cost = sum((c["cost_usd"] or 0.0) for c in cells)
    counts: dict[str, int] = {}
    for c in cells:
        counts[c["status"]] = counts.get(c["status"], 0) + 1

    return {
        "timestamp": timestamp,
        "dry_run": dry_run,
        "defaults": {
            "temperature": config.defaults.temperature,
            "max_tokens": config.defaults.max_tokens,
            "timeout_s": config.defaults.timeout_s,
            "concurrency": config.defaults.concurrency,
        },
        "prompts": [{"id": p.id, "body": p.body} for p in prompts],
        "models": [
            {
                "id": m.id,
                "label": m.label,
                "provider": m.provider,
                "is_local": m.is_local,
                "kind": m.kind,
                "api_base": m.api_base,
                "model_name": m.model_name,
            }
            for m in models
        ],
        "summary": {
            "total": len(cells),
            "total_cost_usd": total_cost,
            "status_counts": counts,
        },
        "cells": cells,
    }
