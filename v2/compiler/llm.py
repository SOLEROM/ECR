"""
LLM backend for the creative stages (distill / expand).

Two providers behind one tiny surface:

  - ``claude``  — shells out to the Claude Code CLI in headless mode
                  (``claude -p <prompt>``); used when available.
  - ``offline`` — a deterministic, network-free heuristic that drafts a *sensible
                  template-default* artifact. It cannot read prose like an LLM, but it
                  always yields a valid, editable checkpoint (R2/R3) so the whole
                  pipeline runs and verifies without a model.

``resolve(pref)`` maps ``auto`` → ``claude`` if the CLI is on PATH, else ``offline``.
The build stage is deterministic and never calls this.
"""

import shutil
import subprocess
from typing import Optional


class LLMUnavailable(RuntimeError):
    pass


def claude_available() -> bool:
    return shutil.which("claude") is not None


def resolve(pref: str) -> str:
    pref = (pref or "auto").lower()
    if pref == "auto":
        return "claude" if claude_available() else "offline"
    return pref


def complete(prompt: str, provider: str, timeout: float = 240.0) -> str:
    """Return the model's completion text, or raise LLMUnavailable for ``offline``/
    when the CLI is missing or errors (callers fall back to their heuristic draft)."""
    if provider != "claude":
        raise LLMUnavailable(f"provider {provider!r} has no text backend")
    if not claude_available():
        raise LLMUnavailable("claude CLI not found on PATH")
    try:
        r = subprocess.run(["claude", "-p", prompt], capture_output=True,
                           text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError) as e:
        raise LLMUnavailable(str(e))
    if r.returncode != 0 or not r.stdout.strip():
        raise LLMUnavailable(f"claude -p failed (rc={r.returncode})")
    return r.stdout


def extract_yaml(text: str) -> Optional[str]:
    """Pull a YAML body out of a model reply (fenced ```yaml block, or the whole text)."""
    if "```" in text:
        parts = text.split("```")
        for i in range(1, len(parts), 2):
            block = parts[i]
            first_nl = block.find("\n")
            lang = block[:first_nl].strip().lower() if first_nl >= 0 else ""
            body = block[first_nl + 1:] if first_nl >= 0 else block
            if lang in ("yaml", "yml", ""):
                return body
    return text if text.strip() else None
