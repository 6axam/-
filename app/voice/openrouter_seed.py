"""OpenRouter transport for ByteDance Seed Audio voice cloning."""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any

import httpx

from app.voice.provider import VoiceGenerationResult, VoiceProvider, VoiceProviderError


log = logging.getLogger(__name__)
MAX_REFERENCE_BYTES = 10 * 1024 * 1024


class OpenRouterSeedAudioProvider(VoiceProvider):
    """Generate a cloned voice through OpenRouter's audio/speech endpoint."""

    name = "openrouter_seed"

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str,
        reference_path: str | Path,
        *,
        timeout_seconds: float = 120,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.reference_path = Path(reference_path)
        self.timeout_seconds = timeout_seconds
        self.transport = transport
        self._reference_b64: str | None = None

    def _reference(self) -> str:
        if self._reference_b64 is not None:
            return self._reference_b64
        if not self.reference_path.is_file():
            raise VoiceProviderError("Voice reference file is unavailable")
        size = self.reference_path.stat().st_size
        if not size:
            raise VoiceProviderError("Voice reference file is empty")
        if size > MAX_REFERENCE_BYTES:
            raise VoiceProviderError("Voice reference file exceeds 10 MB")
        self._reference_b64 = base64.b64encode(self.reference_path.read_bytes()).decode("ascii")
        return self._reference_b64

    @staticmethod
    def _error_kind(response: httpx.Response) -> str:
        """Read an error body without exposing text/reference data to logs."""

        try:
            body: Any = response.json()
        except ValueError:
            return "text_error" if response.text else "empty_error"
        if isinstance(body, dict):
            error = body.get("error", body)
            if isinstance(error, dict):
                value = error.get("type") or error.get("code") or error.get("name")
                if isinstance(value, (str, int, float)):
                    return str(value)[:80]
        return "json_error"

    async def generate(
        self,
        text: str,
        *,
        mood: str | None = None,
        pace: str | None = None,
        energy: float = 0.5,
    ) -> VoiceGenerationResult:
        # Seed's confirmed OpenRouter speech contract has no separate render
        # instruction field.  Do not accidentally make it read prompt text.
        payload = {
            "model": self.model,
            "input": text,
            "response_format": "mp3",
            "reference_audio": self._reference(),
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        log.info("voice_generation_started provider=%s model=%s", self.name, self.model)
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout_seconds, connect=10),
                transport=self.transport,
            ) as client:
                response = await client.post(self.base_url, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            log.warning("voice_generation_failed provider=%s reason=http_error", self.name)
            raise VoiceProviderError("OpenRouter speech request failed") from exc

        if response.is_error:
            kind = self._error_kind(response)
            log.warning(
                "voice_generation_failed provider=%s status=%s error_kind=%s",
                self.name,
                response.status_code,
                kind,
            )
            raise VoiceProviderError(f"OpenRouter speech request failed (HTTP {response.status_code}; {kind})")
        if not response.content:
            raise VoiceProviderError("OpenRouter returned empty speech audio")
        content_type = response.headers.get("content-type", "").lower()
        if not content_type.startswith("audio/mpeg"):
            raise VoiceProviderError("OpenRouter returned unexpected speech content type")
        generation_id = response.headers.get("x-generation-id")
        log.info(
            "voice_generation_completed provider=%s generation_id=%s bytes=%s",
            self.name,
            generation_id or "unknown",
            len(response.content),
        )
        return VoiceGenerationResult(data=response.content, mime_type="audio/mpeg", duration_seconds=None)
