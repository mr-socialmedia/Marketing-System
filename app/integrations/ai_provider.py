"""
The AI layer. Disabled by default, and structurally unable to see raw data.

WHAT THIS IS FOR, AND WHAT IT IS NOT FOR.

The AI improves WORDING. It does not decide what the report is about. Grouping,
classification and deduplication are deterministic and happen before anything
here runs — an AI asked to group five logo files would sometimes be cleverer
and sometimes merge the Platinum logo with the Miss Platinum one because the
names rhyme. The facts are established locally; this makes them read well.

That split is also what keeps the product working with AI switched off, which
is the default. The local Markdown report is complete on its own.

=============================================================================
THE TYPE SIGNATURE IS THE SECURITY CONTROL
=============================================================================

`send()` accepts `list[dict]` produced by `Sanitizer.sanitize_batch()` and
nothing else. There is deliberately no overload taking `WorkItem`, no
`send_raw`, and no `**extra` — so handing a provider an unsanitized object is
a type error at the call site rather than a judgement call in review.

Three gates stand between activity and a provider, and all three must open:

    1. config.ai.enabled          off by default
    2. config.privacy.local_only  ON by default, and refuses all egress
    3. the payload                must have come through the sanitizer

The second is the one that matters most. `local_only` is not a preference: the
security suite severs the socket layer and runs the pipeline, so a provider
that tried to reach the network while it is set would fail the build.

NO SDK IS IMPORTED. Provider SDKs pull in dozens of transitive dependencies,
any one of which can make its own network calls — which would make the
local-only guarantee unverifiable. These speak plain HTTP through urllib, which
is in the standard library and does exactly what it is told.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Any

log = logging.getLogger(__name__)

# Nothing may be sent that has not been through the sanitizer. These are the
# only keys `Sanitizer.sanitize_batch` emits, and `_assert_sendable` refuses a
# payload carrying anything else — so a field added upstream cannot ride along
# unnoticed.
ALLOWED_PAYLOAD_KEYS = frozenset({
    "project", "brand", "category", "action", "file_count",
    "session_count", "date", "duration", "file_types", "confidence",
})


class AIError(RuntimeError):
    """A provider could not be reached, or refused the request."""


class AIRefused(AIError):
    """The request was blocked locally, before any network call."""


def _assert_sendable(payload: list[dict[str, Any]]) -> None:
    """
    Last check before egress. Refuses anything the sanitizer did not produce.

    This duplicates work the sanitizer already did, on purpose. The sanitizer
    guarantees its OUTPUT is clean; this guarantees that what reaches a
    provider is the sanitizer's output and not something assembled nearby that
    looks similar. The two together mean a leak needs both to fail.
    """
    from ..intelligence.sanitizer import Sanitizer

    checker = Sanitizer()
    for index, item in enumerate(payload):
        unexpected = set(item) - ALLOWED_PAYLOAD_KEYS
        if unexpected:
            raise AIRefused(
                f"Item {index} carries field(s) the sanitizer does not emit: "
                f"{', '.join(sorted(unexpected))}. Not sent."
            )
        for key, value in item.items():
            if isinstance(value, str) and checker.contains_path(value):
                # The VALUE is never named in the error — putting it here would
                # write the leaked path into a log file.
                raise AIRefused(f"Item {index} field {key!r} looks like a path. Not sent.")


# ======================================================================
class AIProvider(ABC):
    """
    One operation: turn sanitized work items into better prose.

    Narrow on purpose. A provider with a general `complete(prompt)` method is a
    provider that will eventually be handed a prompt somebody built by string
    concatenation from a database row.
    """

    name: str = "none"

    @abstractmethod
    def available(self) -> bool:
        """Whether this provider is configured enough to be called."""

    @abstractmethod
    def summarise(self, payload: list[dict[str, Any]], period: str) -> str:
        """Sanitized items in, prose out."""

    # ------------------------------------------------------------------
    def send(self, payload: list[dict[str, Any]], period: str) -> str:
        """
        The only entry point. Validates, then delegates.

        Subclasses implement `summarise`; nothing calls it directly, so the
        check below cannot be skipped by a provider that forgets it.
        """
        _assert_sendable(payload)
        if not payload:
            raise AIRefused("Nothing to summarise.")
        return self.summarise(payload, period)


class NullProvider(AIProvider):
    """
    The default. Does nothing, and says so.

    Present rather than using `None` so that every caller has a provider and
    none of them needs an `if provider is not None` branch — the branch that
    eventually gets written as `if provider is None: send_anyway()`.
    """

    name = "none"

    def available(self) -> bool:
        return False

    def summarise(self, payload: list[dict[str, Any]], period: str) -> str:
        raise AIRefused(
            "AI is disabled. The local report is complete without it; enable a "
            "provider in Settings if you want the wording improved."
        )


# ======================================================================
SYSTEM_PROMPT = (
    "You rewrite a marketing department's work items into a short, factual "
    "monthly report.\n"
    "RULES:\n"
    "- Never invent an achievement. Every line must trace to an item given.\n"
    "- Never claim anything was completed. The data cannot establish it.\n"
    "- Never use adjectives that cannot be verified: no 'successfully', "
    "'comprehensive', 'significant', 'strategic'.\n"
    "- Group by discipline, not by project. Projects belong inside the lines.\n"
    "- Third person, past tense, plain English.\n"
    "- You are given counts, not file names. Do not ask for more detail; there "
    "is none, by design."
)


class _HttpProvider(AIProvider):
    """Shared plumbing. No SDK — see the module docstring."""

    endpoint: str = ""
    timeout_seconds: int = 60

    def __init__(self, api_key: str | None, model: str) -> None:
        self._key = api_key
        self._model = model

    def available(self) -> bool:
        return bool(self._key)

    def _post(self, url: str, body: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        import urllib.error
        import urllib.request

        data = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(url, data=data, method="POST")
        for key, value in {"Content-Type": "application/json", **headers}.items():
            request.add_header(key, value)

        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # The response body can echo the request. Never log it.
            raise AIError(f"{self.name} returned HTTP {exc.code}") from None
        except urllib.error.URLError as exc:
            raise AIError(f"{self.name} could not be reached: {exc.reason}") from None
        except (TimeoutError, OSError) as exc:
            raise AIError(f"{self.name} request failed: {exc}") from None


class OpenAIProvider(_HttpProvider):
    name = "openai"

    def __init__(self, api_key: str | None, model: str = "gpt-4o-mini") -> None:
        super().__init__(api_key, model)

    def summarise(self, payload: list[dict[str, Any]], period: str) -> str:
        result = self._post(
            "https://api.openai.com/v1/chat/completions",
            {
                "model": self._model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content":
                        f"Work items for {period}:\n{json.dumps(payload, indent=2)}"},
                ],
                "temperature": 0.2,
            },
            {"Authorization": f"Bearer {self._key}"},
        )
        try:
            return str(result["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise AIError("openai returned an unexpected shape") from exc


class GeminiProvider(_HttpProvider):
    name = "gemini"

    def __init__(self, api_key: str | None, model: str = "gemini-1.5-flash") -> None:
        super().__init__(api_key, model)

    def summarise(self, payload: list[dict[str, Any]], period: str) -> str:
        result = self._post(
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self._model}:generateContent?key={self._key}",
            {
                "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
                "contents": [{"parts": [{"text":
                    f"Work items for {period}:\n{json.dumps(payload, indent=2)}"}]}],
                "generationConfig": {"temperature": 0.2},
            },
            {},
        )
        try:
            return str(result["candidates"][0]["content"]["parts"][0]["text"])
        except (KeyError, IndexError, TypeError) as exc:
            raise AIError("gemini returned an unexpected shape") from exc


class LocalModelProvider(_HttpProvider):
    """
    An OpenAI-compatible server on this machine — Ollama, LM Studio, llama.cpp.

    THE ONLY PROVIDER COMPATIBLE WITH local_only, and the one to prefer. The
    request never leaves the loopback interface, so the privacy posture is
    unchanged while the wording still improves. `local_only` therefore permits
    this one and refuses the others, rather than being all-or-nothing.
    """

    name = "local"

    def __init__(self, base_url: str = "http://127.0.0.1:11434",
                 model: str = "llama3.1") -> None:
        super().__init__(api_key="local", model=model)
        self._base = base_url.rstrip("/")

    def available(self) -> bool:
        return True

    def summarise(self, payload: list[dict[str, Any]], period: str) -> str:
        result = self._post(
            f"{self._base}/v1/chat/completions",
            {
                "model": self._model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content":
                        f"Work items for {period}:\n{json.dumps(payload, indent=2)}"},
                ],
                "temperature": 0.2,
            },
            {},
        )
        try:
            return str(result["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise AIError("the local model returned an unexpected shape") from exc


# ======================================================================
def build_provider(config: Any, api_key: str | None = None) -> AIProvider:
    """
    The configured provider, or the null one.

    REFUSES A REMOTE PROVIDER WHILE local_only IS SET, and says why. Silently
    downgrading to NullProvider would leave somebody wondering for an afternoon
    why their key does nothing; silently honouring the key would break the
    guarantee the rest of the program is built on.

    A LOCAL model is permitted, because loopback is not egress.
    """
    ai = getattr(config, "ai", None)
    if ai is None or not getattr(ai, "enabled", False):
        return NullProvider()

    provider = str(getattr(ai, "provider", "none"))
    local_only = bool(getattr(getattr(config, "privacy", None), "local_only", True))

    if provider == "local":
        return LocalModelProvider(model=getattr(ai, "model", None) or "llama3.1")

    if local_only:
        raise AIRefused(
            f"AI provider {provider!r} would send data off this device while "
            "'local_only' is on. Either use a local model, or turn local_only "
            "off deliberately — it will be audited."
        )

    model = getattr(ai, "model", None)
    if provider == "openai":
        return OpenAIProvider(api_key, model or "gpt-4o-mini")
    if provider == "gemini":
        return GeminiProvider(api_key, model or "gemini-1.5-flash")

    log.warning("Unknown AI provider %r; AI stays disabled.", provider)
    return NullProvider()
