"""llm-check: a local-first LLM test bench.

Reads prompts from prompts/*.txt, runs them against models defined in
config/models.yaml via litellm, and writes one self-contained HTML per
(prompt, model) plus a static dashboard.
"""

__version__ = "0.1.0"
