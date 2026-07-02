"""Write per-model dashboards and the aggregate root dashboard."""
from __future__ import annotations

import json
from importlib import resources
from pathlib import Path


def _read_template_assets() -> tuple[str, str, str]:
    pkg = resources.files("llm_check") / "dashboard"
    template = (pkg / "index.html.tmpl").read_text(encoding="utf-8")
    css = (pkg / "styles.css").read_text(encoding="utf-8")
    js = (pkg / "app.js").read_text(encoding="utf-8")
    return template, css, js


def _write_assets(assets_dir: Path, css: str, js: str) -> None:
    assets_dir.mkdir(parents=True, exist_ok=True)
    (assets_dir / "styles.css").write_text(css, encoding="utf-8")
    (assets_dir / "app.js").write_text(js, encoding="utf-8")


def _inject(template: str, payload: dict) -> str:
    embedded = json.dumps(payload)
    return template.replace("/*__RESULTS_JSON__*/null", embedded)


def render_per_model_dashboard(run_dir: Path) -> Path:
    """Render a single-model dashboard inside `run_dir`."""
    template, css, js = _read_template_assets()
    _write_assets(run_dir / "assets", css, js)
    results = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    # Per-model dashboards are self-contained: html_file paths are relative to
    # the run dir itself (`outputs/...`, `thumbnails/...`).
    html = _inject(template, results)
    out = run_dir / "index.html"
    out.write_text(html, encoding="utf-8")
    return out


def render_dashboard(run_dir: Path) -> Path:
    """Backwards-compatible alias used by `llm-check validate`."""
    return render_per_model_dashboard(run_dir)


# --- Aggregate (root) dashboard --------------------------------------------------

def _collect_runs(runs_root: Path) -> list[tuple[Path, dict]]:
    """Walk runs_root for per-model run dirs and return all of them, newest first.

    Each per-model run dir is identified by containing a `results.json` whose
    `models` list has exactly one entry. Dirs are ranked by their `timestamp`
    field (falling back to mtime).
    """
    found: list[tuple[str, Path, dict]] = []
    for results_path in sorted(runs_root.glob("*/results.json")):
        try:
            data = json.loads(results_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        models = data.get("models") or []
        if len(models) != 1:
            # Skip legacy multi-model runs in the aggregate
            continue
        if not models[0].get("id"):
            continue
        ts = data.get("timestamp") or str(results_path.stat().st_mtime)
        found.append((ts, results_path.parent, data))
    found.sort(key=lambda x: x[0], reverse=True)
    return [(p, d) for _, p, d in found]


def _aggregate_payload(runs_root: Path) -> dict:
    """Build a combined payload by stitching per-model run data together.

    All runs are included, so the same model_id can appear multiple times.
    Models and cells therefore carry a `uid` / `model_uid` of the form
    `{run_dir}::{model_id}` which the dashboard JS uses for indexing.
    Cell html/thumbnail paths are rewritten to be relative to `runs_root`.
    """
    runs = _collect_runs(runs_root)

    prompts_by_id: dict[str, dict] = {}
    models: list[dict] = []
    cells: list[dict] = []
    timestamps: list[str] = []
    dry_run_flags: list[bool] = []

    for run_dir, data in runs:
        rel = run_dir.name  # folder name relative to runs/
        timestamps.append(data.get("timestamp", ""))
        dry_run_flags.append(bool(data.get("dry_run")))

        for p in data.get("prompts") or []:
            prompts_by_id.setdefault(p["id"], p)

        for m in data.get("models") or []:
            enriched = dict(m)
            enriched["uid"] = f"{rel}::{m.get('id')}"
            enriched["run_dir"] = rel
            enriched["run_timestamp"] = data.get("timestamp")
            models.append(enriched)

        for c in data.get("cells") or []:
            cc = dict(c)
            # Per-model results store paths as `outputs/<file>` / `thumbnails/<file>`.
            # In the aggregate, prefix them with the run dir name.
            if cc.get("html_file"):
                cc["html_file"] = f"{rel}/{cc['html_file']}"
            if cc.get("thumbnail"):
                cc["thumbnail"] = f"{rel}/{cc['thumbnail']}"
            if cc.get("raw_file"):
                cc["raw_file"] = f"{rel}/{cc['raw_file']}"
            cc["run_dir"] = rel
            cc["model_uid"] = f"{rel}::{cc.get('model_id')}"
            cells.append(cc)

    # Group rows of the same model together, newest run first within a group.
    models.sort(key=lambda m: m.get("run_timestamp") or "", reverse=True)
    models.sort(key=lambda m: (m.get("label") or m.get("id") or "").lower())

    total_cost = sum((c.get("cost_usd") or 0.0) for c in cells)
    counts: dict[str, int] = {}
    for c in cells:
        counts[c["status"]] = counts.get(c["status"], 0) + 1

    return {
        "aggregate": True,
        "timestamp": max(timestamps) if timestamps else "",
        "dry_run": any(dry_run_flags),
        "prompts": list(prompts_by_id.values()),
        "models": models,
        "cells": cells,
        "summary": {
            "total": len(cells),
            "total_cost_usd": total_cost,
            "status_counts": counts,
            "model_count": len(models),
            "run_count": len(runs),
        },
    }


def render_root_dashboard(runs_root: Path) -> Path:
    """Render `runs/index.html` aggregating all per-model runs."""
    runs_root.mkdir(parents=True, exist_ok=True)
    template, css, js = _read_template_assets()
    _write_assets(runs_root / "assets", css, js)
    payload = _aggregate_payload(runs_root)
    html = _inject(template, payload)
    out = runs_root / "index.html"
    out.write_text(html, encoding="utf-8")
    # Also persist the aggregate payload for debugging / external tooling.
    (runs_root / "results.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out
