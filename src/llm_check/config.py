"""Config and prompt loading."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


@dataclass
class ModelConfig:
    id: str
    provider: str
    label: str
    kind: str | None = None           # free-form label, e.g. a device/host name ("hermine"); shown in dashboards
    api_base: str | None = None
    api_key: str | None = None
    model_name: str | None = None     # name as reported by the server's /v1/models endpoint
    temperature: float | None = None
    max_tokens: int | None = None
    timeout_s: int | None = None
    concurrency: int | None = None
    enabled: bool = True
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def is_local(self) -> bool:
        return bool(self.api_base) or self.provider.startswith("ollama/") or self.provider.startswith("dryrun/")

    @property
    def is_dry_run(self) -> bool:
        return self.provider.startswith("dryrun/")

    @property
    def is_openai_compatible_local(self) -> bool:
        """True for local OpenAI-compatible servers (llama.cpp, LM Studio, vLLM, ...)."""
        return bool(self.api_base) and self.provider.startswith("openai/")


@dataclass
class Defaults:
    temperature: float = 0.7
    max_tokens: int = 8000
    timeout_s: int = 300
    concurrency: int = 4


@dataclass
class BenchConfig:
    defaults: Defaults
    models: list[ModelConfig]


@dataclass
class Prompt:
    id: str
    path: Path
    body: str


def load_env(workspace: Path) -> None:
    """Load .env if present. Silent if missing - local models don't need it."""
    env_path = workspace / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=False)


KIND_MAX_LEN = 20


def _clean_kind(value: Any) -> str | None:
    """Normalize a free-form `kind` label: coerce to str, strip, cap at 20 chars.

    Any string is accepted (e.g. a device/host name like "hermine"). Empty or
    missing values become None.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:KIND_MAX_LEN]


def load_config(path: Path) -> BenchConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    defaults_raw = raw.get("defaults") or {}
    defaults = Defaults(
        temperature=float(defaults_raw.get("temperature", 0.7)),
        max_tokens=int(defaults_raw.get("max_tokens", 8000)),
        timeout_s=int(defaults_raw.get("timeout_s", 300)),
        concurrency=int(defaults_raw.get("concurrency", 4)),
    )
    models: list[ModelConfig] = []
    for entry in raw.get("models") or []:
        models.append(
            ModelConfig(
                id=entry["id"],
                provider=entry["provider"],
                label=entry.get("label", entry["id"]),
                kind=_clean_kind(entry.get("kind")),
                api_base=entry.get("api_base"),
                api_key=entry.get("api_key"),
                model_name=entry.get("model_name"),
                temperature=entry.get("temperature"),
                max_tokens=entry.get("max_tokens"),
                timeout_s=entry.get("timeout_s"),
                concurrency=entry.get("concurrency"),
                enabled=bool(entry.get("enabled", True)),
                extra={
                    k: v
                    for k, v in entry.items()
                    if k
                    not in {
                        "id",
                        "provider",
                        "label",
                        "kind",
                        "api_base",
                        "api_key",
                        "model_name",
                        "temperature",
                        "max_tokens",
                        "timeout_s",
                        "concurrency",
                        "enabled",
                    }
                },
            )
        )
    return BenchConfig(defaults=defaults, models=models)


def load_prompts(prompts_dir: Path) -> list[Prompt]:
    prompts: list[Prompt] = []
    for path in sorted(prompts_dir.glob("*.txt")):
        body = path.read_text(encoding="utf-8").strip()
        if not body:
            continue
        prompts.append(Prompt(id=path.stem, path=path, body=body))
    return prompts


# Map known providers to the env var litellm expects. Used for the preflight
# "do we have a key?" check. Anything not in here is assumed to be local /
# OpenAI-compatible and is allowed through.
PROVIDER_ENV_VAR: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GOOGLE_API_KEY",
    "vertex_ai": "GOOGLE_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "groq": "GROQ_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "cohere": "COHERE_API_KEY",
}


def provider_prefix(provider: str) -> str:
    return provider.split("/", 1)[0]


def required_env_var(model: ModelConfig) -> str | None:
    """Return the env var name needed for a cloud model, or None for local."""
    if model.is_local:
        return None
    return PROVIDER_ENV_VAR.get(provider_prefix(model.provider))


def has_api_key(model: ModelConfig) -> bool:
    if model.api_key:
        return True
    var = required_env_var(model)
    if var is None:
        return True  # local or unknown-cloud (let litellm decide)
    return bool(os.environ.get(var))
