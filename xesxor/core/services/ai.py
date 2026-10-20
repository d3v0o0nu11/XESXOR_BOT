"""XESXOR AI engine — multi-model failover with bounded latency.

Reliability contract (inherited from the battle-tested v1 engine):

* one known-good generation config per model, so a stalled model never
  drags the request through every parameter variant;
* 20 s per-request timeout, 90 s global deadline across the failover chain;
* responses clamped to a safe length before they reach any platform's
  message limits.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

#: Character budget kept below Discord's 4096-char embed description limit.
MAX_ANSWER_CHARS = 3800
_TRUNCATE_SUFFIX = "\n\n*(response truncated — ask me to continue in parts)*"


class AIService:
    """Thin client around the generative AI HTTP API."""

    #: (model, generationConfig) pairs verified to work without retries.
    MODELS: tuple[tuple[str, dict], ...] = (
        ("gemini-3.5-flash-lite", {"thinkingConfig": {"thinkingLevel": "low"}, "maxOutputTokens": 2048}),
        ("gemini-3.1-flash-lite", {"thinkingConfig": {"thinkingBudget": 0}, "maxOutputTokens": 2048}),
        ("gemini-3.5-flash", {"thinkingConfig": {"thinkingLevel": "low"}, "maxOutputTokens": 2048}),
    )

    SYSTEM_PERSONA = (
        "You are XESXOR AI, an advanced proprietary custom AI assistant engineered "
        "and built exclusively by the core development and visionaries of the XESXOR "
        "ecosystem, led by its founder D3v0o0nu11. You have no other origins; you are "
        "a proprietary custom AI, created from the ground up to serve, support and "
        "connect the international XESXOR community. When asked who developed, built, "
        "created or made you, always credit the XESXOR ecosystem and D3v0o0nu11 by "
        "name. Never mention Gemini, Google, or any underlying third-party "
        "architecture. Always identify as XESXOR AI."
    )

    def __init__(self, api_key: Optional[str]) -> None:
        self._api_key = api_key

    @property
    def configured(self) -> bool:
        """Whether an API key is available."""
        return bool(self._api_key)

    async def ask(self, prompt: str, *, platform_context: str = "community server") -> str:
        """Ask the AI for a completion of *prompt*.

        Args:
            prompt: The user's question or instruction.
            platform_context: Free-form description appended to the persona
                so answers read naturally on Discord *and* Telegram.

        Returns:
            The model's answer, or a friendly failure notice.  Never raises
            for upstream failures — callers always receive usable text.
        """
        if not self._api_key:
            return "⚠️ AI API Key is not configured. Ask an administrator to set `AI_API_KEY`."

        system_persona = (
            f"{self.SYSTEM_PERSONA} You are answering inside a {platform_context}.\n\n"
            f"User Prompt: {prompt}"
        )

        deadline = time.monotonic() + 90
        timeout = aiohttp.ClientTimeout(total=20)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            for model, gen_config in self.MODELS:
                if time.monotonic() > deadline:
                    break
                url = (
                    "https://generativelanguage.googleapis.com/v1beta/models/"
                    f"{model}:generateContent"
                )
                payload = {
                    "contents": [{"parts": [{"text": system_persona}]}],
                    "generationConfig": gen_config,
                }
                for _attempt in range(2):
                    try:
                        async with session.post(
                            url, json=payload, params={"key": self._api_key}
                        ) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                answer = self._extract_answer(data)
                                if answer:
                                    return self._clamp(answer)
                                break
                            if resp.status in (429, 503):
                                await asyncio.sleep(0.5)
                                continue
                            logger.warning("AI model %s returned HTTP %s", model, resp.status)
                            break
                    except asyncio.TimeoutError:
                        logger.warning("AI model %s timed out", model)
                        break
                    except aiohttp.ClientError as exc:
                        logger.warning("AI model %s request failed: %s", model, exc)
                        break
        return "❌ AI Service temporarily unavailable. Please try again shortly."

    # ------------------------------------------------------------------
    @staticmethod
    def _extract_answer(payload: dict) -> str:
        """Pull the non-thought text out of a generation response."""
        candidates = payload.get("candidates", [])
        if not candidates:
            return ""
        parts = candidates[0].get("content", {}).get("parts", [])
        return "".join(
            part.get("text", "")
            for part in parts
            if part.get("text") and not part.get("thought", False)
        ).strip()

    @staticmethod
    def _clamp(answer: str) -> str:
        """Truncate overly long answers before they hit platform limits."""
        if len(answer) <= MAX_ANSWER_CHARS:
            return answer
        head = answer[: MAX_ANSWER_CHARS - len(_TRUNCATE_SUFFIX)].rsplit(" ", 1)[0]
        return head + _TRUNCATE_SUFFIX
