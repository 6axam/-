import asyncio
import base64
import json
import logging
import time
from dataclasses import dataclass

import httpx
from pydantic import ValidationError

from app.llm.base import LLMProvider, ProviderError, StructuredOutputError
from app.llm.schemas import DailyLifeDecision, ImageContent, InitiativeDecision, LLMRequest, LLMResponse, ResponseTiming, StickerSemantics, TextContent
from app.llm.prompts import read_prompt

log = logging.getLogger(__name__)
FORMAT = {"type": "json_object"}
REPAIR_PROMPT = "Return only valid JSON matching this schema. Do not add markdown. Schema: " + json.dumps(LLMResponse.model_json_schema(), ensure_ascii=False)

@dataclass(frozen=True)
class Completion:
    content: str
    usage: dict | None
    latency_ms: int


class OpenAICompatibleProvider(LLMProvider):
    default_base_url = "https://api.openai.com/v1"

    def __init__(self, api_key: str, model: str, base_url: str | None = None, *, transport=None, retries: int = 2, supports_vision: bool = True, supports_multiple_images: bool = True):
        self.api_key, self.model = api_key, model
        self.base_url = (base_url or self.default_base_url).rstrip("/")
        self.transport, self.retries = transport, retries
        self.supports_vision = supports_vision
        self.supports_multiple_images = supports_multiple_images

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    async def _complete(self, messages: list[dict]) -> Completion:
        payload = {"model": self.model, "messages": messages, "response_format": FORMAT}
        error = None
        started = time.perf_counter()
        for attempt in range(self.retries + 1):
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=10), transport=self.transport) as client:
                    response = await client.post(self.base_url + "/chat/completions", headers=self.headers(), json=payload)
                if response.status_code >= 500:
                    raise httpx.HTTPStatusError("temporary provider error", request=response.request, response=response)
                response.raise_for_status()
                body = response.json()
                return Completion(body["choices"][0]["message"]["content"], body.get("usage"), round((time.perf_counter() - started) * 1000))
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code < 500:
                    raise ProviderError(f"LLM API rejected the request ({exc.response.status_code})") from exc
                error = exc
                if attempt < self.retries:
                    await asyncio.sleep(.25 * (attempt + 1))
            except (httpx.TimeoutException, httpx.NetworkError, KeyError, ValueError) as exc:
                error = exc
                if attempt < self.retries:
                    await asyncio.sleep(.25 * (attempt + 1))
        raise ProviderError(f"LLM request failed after {self.retries + 1} attempts: {error}") from error

    @staticmethod
    def _parse(raw: str) -> LLMResponse:
        try:
            return LLMResponse.model_validate_json(raw)
        except (ValidationError, ValueError) as exc:
            raise StructuredOutputError("LLM returned invalid structured output") from exc

    def _user_content(self, request: LLMRequest) -> str | list[dict]:
        text = request.context + "\n\nUSER TURN:\n" + request.user_turn
        images = [part for part in request.user_content if isinstance(part, ImageContent)]
        if not images or not self.supports_vision:
            return text
        if not self.supports_multiple_images:
            images = images[:1]
        content: list[dict] = [{"type": "text", "text": text}]
        for image in images:
            encoded = base64.b64encode(image.data).decode("ascii")
            content.append({"type": "image_url", "image_url": {"url": f"data:{image.mime_type};base64,{encoded}"}})
        return content

    async def generate(self, request: LLMRequest) -> LLMResponse:
        if any(isinstance(part, ImageContent) for part in request.user_content):
            log.info("image_vision_requested images=%s", len([part for part in request.user_content if isinstance(part, ImageContent)]))
        messages = [
            {"role": "system", "content": request.system + "\n" + REPAIR_PROMPT},
            {"role": "user", "content": self._user_content(request)},
        ]
        completed = await self._complete(messages); raw = completed.content
        if completed.usage or any(isinstance(part, ImageContent) for part in request.user_content):
            log.info("llm_call_completed kind=conversation model=%s latency_ms=%s images=%s usage=%s", self.model, completed.latency_ms, len([part for part in request.user_content if isinstance(part, ImageContent)]), completed.usage)
        try:
            return self._parse(raw)
        except StructuredOutputError:
            log.warning("Invalid LLM JSON; attempting one repair")
            repair = (await self._complete([
                {"role": "system", "content": REPAIR_PROMPT},
                {"role": "user", "content": raw},
            ])).content
            try:
                return self._parse(repair)
            except StructuredOutputError:
                log.exception("LLM JSON repair failed")
                raise

    async def decide_timing(self, request: LLMRequest) -> ResponseTiming:
        prompt = "Return only JSON: {\"mode\": \"immediate\"|\"delayed\", \"urgency\": \"urgent\"|\"normal\"|\"low\"}. Choose delayed only when an abstract availability rhythm is appropriate; never invent a concrete activity."
        try:
            raw = (await self._complete([{"role": "system", "content": prompt}, {"role": "user", "content": request.context + "\nUSER TURN:\n" + request.user_turn}])).content
            return ResponseTiming.model_validate_json(raw)
        except Exception:
            log.warning("Timing decision unavailable; responding immediately", exc_info=True)
            return ResponseTiming()

    async def decide_initiative(self, request: LLMRequest) -> InitiativeDecision:
        prompt = "Return only JSON matching this initiative decision schema: " + json.dumps(InitiativeDecision.model_json_schema(), ensure_ascii=False) + "\nDo not message merely because time passed. Do not guilt-trip, pressure, or ask why the user is absent. should_message=false is normal. reason is internal only."
        try:
            raw = (await self._complete([{"role": "system", "content": prompt}, {"role": "user", "content": request.context}])).content
            return InitiativeDecision.model_validate_json(raw)
        except Exception:
            log.warning("Initiative decision unavailable; skipping", exc_info=True)
            return InitiativeDecision(should_message=False, reason="initiative decision unavailable")

    async def decide_daily_life(self, request: LLMRequest) -> DailyLifeDecision:
        prompt = "Return only JSON matching this schema: " + json.dumps(DailyLifeDecision.model_json_schema(), ensure_ascii=False) + "\nCreate an event rarely. It must be a mundane, plausible current activity compatible with the supplied schedule; never invent drama or an excuse after an absence. create_event=false is normal."
        try:
            raw = (await self._complete([{"role":"system","content":prompt},{"role":"user","content":request.context}])).content
            return DailyLifeDecision.model_validate_json(raw)
        except Exception:
            log.warning("daily_life_decision_unavailable", exc_info=True)
            return DailyLifeDecision()

    async def analyze_sticker(self, frames: list[ImageContent]) -> StickerSemantics:
        if not self.supports_vision:
            raise ProviderError("Vision is disabled for the configured provider/model")
        if not frames:
            raise ProviderError("Sticker analysis requires at least one frame")
        prompt = (
            read_prompt("sticker_analyzer.md") + "\n\nReturn only JSON matching this schema: "
            + json.dumps(StickerSemantics.model_json_schema(), ensure_ascii=False)
            + " Focus on concise literal visual description AND pragmatic chat meaning."
        )
        request = LLMRequest(system=prompt, context="", user_turn="Analyze the sticker.", user_content=frames)
        completed = await self._complete([{"role": "system", "content": prompt}, {"role": "user", "content": self._user_content(request)}]); raw = completed.content
        log.info("llm_call_completed kind=sticker_vision model=%s latency_ms=%s images=%s usage=%s", self.model, completed.latency_ms, len(frames), completed.usage)
        try:
            return StickerSemantics.model_validate_json(raw)
        except (ValidationError, ValueError) as exc:
            log.warning("Invalid sticker analyzer JSON; attempting one repair")
            repair = (await self._complete([{"role": "system", "content": "Return only valid JSON matching this schema: " + json.dumps(StickerSemantics.model_json_schema(), ensure_ascii=False)}, {"role": "user", "content": raw}])).content
            try:
                return StickerSemantics.model_validate_json(repair)
            except (ValidationError, ValueError) as repair_exc:
                raise StructuredOutputError("Sticker analyzer returned invalid JSON after repair") from repair_exc

    async def describe_image(self, image: ImageContent) -> str:
        if not self.supports_vision: raise ProviderError("Vision is disabled for the configured provider/model")
        request = LLMRequest(system="Describe this photo in one concise factual sentence for private conversation history. Return only JSON: {\"description\": \"...\"}.", context="", user_turn="Describe the image.", user_content=[image])
        completed = await self._complete([{"role": "system", "content": request.system}, {"role": "user", "content": self._user_content(request)}])
        try:
            description = json.loads(completed.content)["description"]
            if not isinstance(description, str) or not description.strip(): raise ValueError("empty description")
            log.info("llm_call_completed kind=photo_vision model=%s latency_ms=%s images=1 usage=%s", self.model, completed.latency_ms, completed.usage)
            return description.strip()[:500]
        except Exception as exc:
            raise StructuredOutputError("Photo description returned invalid JSON") from exc


OpenAIProvider = OpenAICompatibleProvider
