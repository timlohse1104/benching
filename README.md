# benching

Benchmark tests for AI and other things. This repo currently hosts **llm-check**, a local-first LLM test bench: drop prompts as `.txt` files, list the models you want to compare in a YAML config, and the bench produces one self-contained `.html` per (prompt × model) plus a static dashboard with overview, 2-way compare, and broken-HTML detector.

Built for the workflow "I have a prompt that asks an LLM to produce a self-contained HTML page. I want to see how different models do, side by side."

## Highlights

- Local-first: llama.cpp (and any other OpenAI-compatible local server) works out of the box, no API keys needed.
- Cloud-optional: drop a key in `.env` and uncomment the model in `config/models.yaml`.
- One `.txt` per prompt. No metadata, no front-matter, no DSL.
- Two-layer validator: strict HTML parser (`html5lib`) + headless Chromium (`playwright`) that captures runtime errors and a thumbnail.
- Static dashboard: pure `index.html` you open from disk. Overview matrix, 2-way side-by-side compare, error explorer.
- Dry-run mode: smoke-test the whole pipeline with built-in canned responses (no network, no LLM).

## Quickstart (local, llama.cpp)

On Debian/Ubuntu (PEP 668) you cannot `pip install` into the system Python. Use a venv:

```bash
# 1. Create and activate a virtualenv (one-time)
python3 -m venv .venv
source .venv/bin/activate

# 2. Install Python deps
pip install -e .

# 3. Install the headless browser used by the validator
playwright install chromium

# 4. Create your local model registry from the example
cp config/models.example.yaml config/models.yaml   # then edit to taste

# 5. Start a llama.cpp OpenAI-compatible server in another terminal, e.g.:
#    llama-server -m /path/to/model.gguf --host 127.0.0.1 --port 8080
#    (default api_base in config/models.yaml is http://localhost:8080/v1)

# 6. Run the bench
llm-check run

# 7. Open the printed dashboard path in your browser
```

In future terminals, just `source .venv/bin/activate` before running `llm-check`. Or call the binary directly without activating: `./.venv/bin/llm-check run`.

No `.env` is needed for local models.

## Quickstart (dry-run, no LLM)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
playwright install chromium
llm-check run --dry-run
```

The dry-run provider produces a deterministic mix of valid and intentionally-broken HTML so every dashboard view has something to show.

## Adding a prompt

Drop a plain `.txt` file in `prompts/`. The filename (without `.txt`) becomes the prompt id. The file body is sent as the user message. A small built-in system prompt tells the model to respond with one self-contained HTML5 document and nothing else.

## Adding a model

Edit `config/models.yaml` (create it once with `cp config/models.example.yaml config/models.yaml`; the real file is gitignored):

```yaml
models:
  - id: llamacpp-coder
    provider: openai/llamacpp           # any name after `openai/`; llama.cpp ignores it
    label: "llama.cpp coder (local)"
    kind: workstation                   # optional free-form badge, e.g. the host it runs on
    api_base: http://localhost:8081/v1  # llama-server --port 8081
    api_key: not-needed                 # required by litellm; server ignores it
    model_name: "qwen2.5-coder-7b-instruct-q5_k_m.gguf"   # exact name from /v1/models
    concurrency: 1
    enabled: true
```

`model_name` is the string the server returns at `GET {api_base}/models` (the `id` field for each entry in `data`). The preflight queries that endpoint and shows you the served name. You can leave `model_name` blank to auto-adopt whatever the server is currently serving.

`kind` is an optional free-form label (any string, capped at 20 characters) — typically the device or host the model runs on, e.g. `hermine` or `localhost`. It is purely cosmetic: it does not affect local/cloud classification (that stays auto-detected from `provider`/`api_base`), it is only shown as a badge next to the model in the per-run dashboard and the aggregate overview. Omit it to show no badge.

### Multiple llama.cpp models

A single `llama-server` serves exactly one loaded model at a time. Two patterns:

- Multiple servers: run `llama-server` on different ports and add one YAML entry per port (each with its own `api_base` and `model_name`). They run in parallel.
- One server, swap between runs: keep multiple entries against the same `api_base` but only enable the one matching the currently loaded `.gguf`. Entries whose `model_name` is not currently served are skipped with a clear message.

All cells targeting the same `api_base` are automatically serialized (effective concurrency = 1 per endpoint), regardless of the global `concurrency` setting.

For cloud models, uncomment one of the examples in the file and put the matching key in `.env` (see `.env.example`). Use any model string `litellm` accepts (`openai/gpt-4o`, `anthropic/claude-...`, `gemini/...`, `openrouter/...`, etc.).

### Concurrency

`defaults.concurrency` caps total in-flight requests for the whole run. Each model can override with its own `concurrency` (most local single-GPU setups want `1`). The runner takes `min(global, per-model)` per call.

## Commands

```bash
llm-check list                       # show prompts and models, with reachability
llm-check run                        # run the full matrix
llm-check run --dry-run              # use canned responses, no network
llm-check run -p 01-landing-page     # filter prompts (repeatable)
llm-check run -m llamacpp            # filter models (repeatable)
llm-check validate                   # re-render runs/index.html (aggregate)
llm-check validate runs/<run>        # re-render a single per-model dashboard
```

## Output layout

Each model gets its own run directory. The top-level `runs/index.html` aggregates the latest run per model into a single overview/compare/error view.

```
runs/
├── index.html                                    # aggregate dashboard (open this)
├── results.json                                  # aggregate payload (for debugging)
├── assets/                                       # shared css + js
├── 2026-06-30T22-12-00Z__llamacpp-ornith/        # one run dir PER model
│   ├── index.html                                # single-model dashboard
│   ├── results.json
│   ├── outputs/                                  # prompt × this model
│   └── thumbnails/
└── 2026-06-30T22-12-00Z__llamacpp-qwen/
    └── ...
```

The aggregate dashboard reads the per-model `results.json` files on disk and shows them all side-by-side. Re-running a single model leaves other models' runs intact; the aggregate is re-rendered each time and always reflects the latest run per `model_id`.

## How the broken-HTML detector works

Two passes per output:

1. Parser pass (`html5lib`, lossless): missing `<!DOCTYPE>`, missing `html/head/body`, parse errors with line/col, duplicate IDs.
2. Headless pass (`playwright` + Chromium): loads the file via `file://`, captures `console.error`, `pageerror`, and failed subresource requests. Also writes a 1280×800 thumbnail.

Status per cell:
- `ok` - no issues
- `warnings` - parser issues only (page still renders)
- `broken` - runtime JS or page error
- `failed` - the LLM request itself failed (network, timeout, no key)

## Dashboard

Open `runs/<timestamp>/index.html` in any browser - it works straight off disk.

- Overview: matrix of prompts × models with status badges, latency, tokens, cost, and a hover-ready thumbnail. Click any cell to open the rendered output in a modal.
- Compare: pick a prompt, then two models. Both outputs render in sandboxed iframes side by side. Validator details collapse below each side.
- Errors: every flagged output grouped by error kind, with one click to open the offending file.

Keyboard: `1` / `2` / `3` switch tabs, `/` focuses the active tab's filter, `Esc` closes the modal.

## Folder reference

```
config/models.example.yaml  # template model registry (tracked)
config/models.yaml          # your model registry (gitignored; copy from example)
prompts/*.txt           # the prompt suite
src/llm_check/          # bench source
runs/                   # outputs (gitignored)
.env.example            # cloud keys template
```
