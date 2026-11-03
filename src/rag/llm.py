"""Swappable text-generation layer for the policy assistant.

Only a *local* model is supported on purpose (data-protection policy: no customer
or employee text leaves the bank network). The default is "no LLM": answers are
then extracted directly from the policy text.
"""

from __future__ import annotations

import ipaddress
import json
import re
import urllib.error
import urllib.request
from functools import lru_cache
from typing import Protocol
from urllib.parse import urlparse

from src.core.config import get_settings


class LlmUnavailable(RuntimeError):
    """The model server is not reachable, timed out or returned garbage."""


class LlmClient(Protocol):
    name: str

    def generate(self, system: str, user: str) -> str: ...


def validate_local_url(url: str) -> str:
    """Accept only loopback, private-network or single-label hosts."""
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if parsed.scheme not in {"http", "https"} or not host:
        raise ValueError("LLM_BASE_URL غير صالح.")
    if host == "localhost" or "." not in host:
        return url.rstrip("/")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if host.endswith((".local", ".internal", ".lan")):
            return url.rstrip("/")
        raise ValueError("يجب أن يكون خادم النموذج محليًا (localhost أو شبكة داخلية).") from None
    if address.is_loopback or address.is_private:
        return url.rstrip("/")
    raise ValueError("يجب أن يكون خادم النموذج محليًا (localhost أو شبكة داخلية).")


_THINK = re.compile(r"<think>.*?</think>", flags=re.DOTALL)


class OllamaClient:
    """Talks to a local Ollama server (e.g. ``ollama pull qwen2.5:7b-instruct``)."""

    name = "ollama"

    def __init__(self, base_url: str, model: str, timeout_seconds: int = 90) -> None:
        self.base_url = validate_local_url(base_url)
        self.model = model
        self.timeout_seconds = timeout_seconds

    def generate(self, system: str, user: str) -> str:
        body = json.dumps(
            {
                "model": self.model,
                "stream": False,
                "options": {"temperature": 0, "num_predict": 400},
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310
                payload = json.loads(response.read().decode("utf-8"))
            content = payload["message"]["content"]
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError, TypeError) as exc:
            raise LlmUnavailable("تعذر الاتصال بنموذج اللغة المحلي.") from exc
        if not isinstance(content, str):
            raise LlmUnavailable("استجابة غير متوقعة من نموذج اللغة.")
        return _THINK.sub("", content).strip()


@lru_cache(maxsize=1)
def get_llm_client() -> LlmClient | None:
    """Return the configured client, or None when no LLM is configured."""
    settings = get_settings()
    if settings.llm_provider == "ollama":
        return OllamaClient(settings.llm_base_url, settings.llm_model, settings.llm_timeout_seconds)
    return None
