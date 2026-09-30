import base64, json, logging, random, time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import httpx
from app.actions.models import ImageKind

log = logging.getLogger(__name__)

@dataclass(frozen=True)
class GeneratedImage:
    data: bytes
    mime_type: str = "image/png"
    cost: float | None = None

class ImageGenerationProvider(ABC):
    name="disabled"; model=""
    @abstractmethod
    async def generate(self, prompt: str, references: list[bytes] | None = None) -> GeneratedImage: ...

class DisabledImageGenerationProvider(ImageGenerationProvider):
    async def generate(self, prompt, references=None): raise RuntimeError("image generation provider is disabled")

class OpenAIImageProvider(ImageGenerationProvider):
    name="openai_compatible"
    def __init__(self,key,model,base_url): self.api_key,self.model,self.base_url=key,model,base_url.rstrip("/")
    async def generate(self,prompt,references=None):
        async with httpx.AsyncClient(timeout=httpx.Timeout(90,connect=10)) as c:
            r=await c.post(self.base_url+"/images/generations",headers={"Authorization":f"Bearer {self.api_key}"},json={"model":self.model,"prompt":prompt,"n":1,"size":"1024x1024","response_format":"b64_json"})
        r.raise_for_status(); return GeneratedImage(base64.b64decode(r.json()["data"][0]["b64_json"]))

class OpenRouterImageProvider(OpenAIImageProvider):
    name="openrouter"
    async def generate(self,prompt,references=None):
        # OpenRouter Image API requires the same typed image_url object for
        # every input reference; a bare `data_url` object is rejected with 400.
        payload={"model":self.model,"prompt":prompt}
        if references: payload["input_references"]=[{"type":"image_url","image_url":{"url":"data:image/jpeg;base64,"+base64.b64encode(x).decode()}} for x in references]
        started=time.perf_counter()
        async with httpx.AsyncClient(timeout=httpx.Timeout(90,connect=10)) as c:
            r=await c.post(self.base_url+"/images",headers={"Authorization":f"Bearer {self.api_key}","Content-Type":"application/json"},json=payload)
        if r.is_error:
            safe_body=r.text[:4000]
            try: parsed=r.json()
            except ValueError: parsed=None
            log.error("openrouter_image_error status=%s model=%s endpoint=%s reference_used=%s references=%s response=%s parsed=%s",r.status_code,self.model,self.base_url+"/images",bool(references),len(references or []),safe_body,parsed)
            r.raise_for_status()
        body=r.json(); item=body["data"][0]
        log.info("image_provider_completed model=%s reference_used=%s latency_ms=%s",self.model,bool(references),round((time.perf_counter()-started)*1000))
        return GeneratedImage(base64.b64decode(item["b64_json"]),item.get("media_type","image/png"),(body.get("usage") or {}).get("cost"))

class ImagePromptBuilder:
    outfits = {
      "college": [
        "black flared trousers, fitted dark top, subtle flared sleeves, dark boots",
        "long dark skirt, simple fitted black top, dark boots",
        "black flared trousers, dark long-sleeve top, black boots",
      ],
      "home": [
        "oversized dark T-shirt, soft pink Hello Kitty pajama pants, barefoot",
        "loose ordinary T-shirt, comfortable home trousers, black-painted nails visible when natural",
        "oversized T-shirt, pink Hello Kitty pajama pants, soft house slippers with small green alien-cat details",
      ],
      "sleep": ["relaxed sleepwear appropriate for home and the time of night"],
      "outside": [
        "dark feminine everyday outfit with black flared trousers and a fitted dark top",
        "long dark skirt, dark top and practical dark boots",
      ],
    }
    kinds={
      "front_selfie":["front camera selfie, head and shoulders, phone is not visible","front camera held slightly above eye level, head and upper torso, arm partly visible","front-camera selfie whose relaxed pose and framing follow the requested situation; if she is in bed, she is naturally lying or sitting in bed"],
      "mirror_selfie":["mirror reflection, waist-up, phone directly in front of lower half of face","mirror reflection, three-quarter body, phone covers most of face, free hand near hip","quick imperfect mirror reflection; pose, crop and surrounding detail follow the requested situation"],
      "casual_photo":["casual candid personal snapshot, natural standing pose"],
      "outfit_photo":["mirror reflection, full-body outfit check, phone visible in mirror"],
      "object_photo":["ordinary close phone photo of the object, no person"],
      "environment_photo":["ordinary candid phone photo of the place, no person"],
      "meme":["internet meme / absurd visual joke, no photographic identity requirement"],
    }
    def __init__(self,db,timezone_name="Europe/Kyiv",reference_path="assets/anya/reference.jpg",debug=False,rng=None,weather=None):
        self.db,self.zone,self.reference_path,self.debug,self.rng,self.weather=db,ZoneInfo(timezone_name),Path(reference_path),debug,rng or random.Random(),weather
    async def build(self,chat_id,intent):
        now=datetime.now(self.zone); college=now.weekday()<5 and 8<=now.hour<14
        weather = await self.weather.current() if self.weather else None
        location,activity,outfit_key=("college classroom","classes","college") if college else (("home bedroom","sleeping","sleep") if now.hour<8 else ("home bedroom","free time","home"))
        clothing=self.rng.choice(self.outfits[outfit_key])
        is_outdoors = college
        event=await self.db.fetchone("SELECT title,availability FROM daily_events WHERE chat_id=? AND julianday(starts_at)<=julianday('now') AND julianday(ends_at)>julianday('now') ORDER BY ends_at DESC LIMIT 1",(chat_id,))
        if event:
            location=activity=event["title"]
            is_outdoors = event["availability"] == "away"
            if event["availability"] in {"busy","away"}:
                clothing=self.rng.choice(self.outfits["outside"]); outfit_key="outside"
        # Temperature changes clothing only for a new outdoor/college period.
        if weather and is_outdoors:
            if weather.apparent_temperature_c <= 8: clothing += ", plus a warm dark coat or jacket"
            elif weather.apparent_temperature_c <= 17: clothing += ", plus a dark jacket"
        key=f"{now.date()}:{location}:{clothing}"; state=await self.db.fetchone("SELECT * FROM visual_state WHERE chat_id=?",(chat_id,))
        if state and state["period_key"]==key: location,activity,clothing=state["location"],state["activity"],state["clothing_context"]
        else: await self.db.execute("INSERT INTO visual_state(chat_id,location,activity,clothing_context,period_key) VALUES(?,?,?,?,?) ON CONFLICT(chat_id) DO UPDATE SET location=excluded.location,activity=excluded.activity,clothing_context=excluded.clothing_context,period_key=excluded.period_key,updated_at=CURRENT_TIMESTAMP",(chat_id,location,activity,clothing,key))
        kind = intent.kind.value
        if kind not in self.kinds:
            # This is defensive: ImageIntent normally rejects unknown values
            # before an action reaches the executor.
            raise ValueError(f"unsupported image intent kind: {kind}")
        self_present=kind in {"front_selfie","mirror_selfie","casual_photo","outfit_photo"}
        appearance=Path("prompts/appearance.md").read_text(encoding="utf-8").strip() if Path("prompts/appearance.md").exists() else ""
        if self_present and not appearance: raise ValueError("appearance.md must be filled for images containing Anya")
        variant=self.rng.choice(self.kinds[kind])
        env=("ordinary lived-in college setting appropriate to the exact scene; institutional furniture and personal items only where naturally visible" if "college" in location else "ordinary lived-in home setting appropriate to the exact scene; select only a few natural nearby details, do not force a bed, desk, laptop or clutter into every frame")
        style_path=Path("prompts/photo_style.md"); style=style_path.read_text(encoding="utf-8").strip() if style_path.exists() else "private Telegram smartphone photo, imperfect framing"
        light = "night/evening: no daylight from windows; interior artificial lighting and dark exterior" if now.hour >= 20 or now.hour < 6 else "daytime lighting appropriate to the current place"
        weather_block = f"CURRENT WEATHER (real external data)\n{weather.describe()}; use it only for outdoor light and weather-appropriate outerwear." if weather else "CURRENT WEATHER\nUnavailable — do not invent weather."
        custom_path=Path("prompts/image_custom.md")
        custom=custom_path.read_text(encoding="utf-8").strip() if custom_path.exists() else ""
        parts=["SITUATION\n"+intent.scene+"\nTreat this as the primary moment of the image. Adapt pose, crop and nearby environment to it, while keeping the persistent world state.","CURRENT VISUAL STATE\n"+f"{now:%Y-%m-%d %H:%M}, {location}; {activity}",weather_block,"LIGHTING\n"+light,"ENVIRONMENT\n"+env,"CAMERA / COMPOSITION\n"+variant,"PHOTO CHARACTER\n"+style,"AVOID\nprofessional photography, fashion shoot, cinematic lighting, studio composition, glamour retouching, beauty-ad aesthetic, unexplained third-person photographer"]
        if self_present: parts.insert(0,"IDENTITY / APPEARANCE\n"+appearance+"\nRequire dark brown eyes; preserve identity from canonical reference."); parts.insert(2,"OUTFIT\n"+clothing)
        if custom: parts.append("USER VISUAL PREFERENCES\n"+custom+"\nApply these only when compatible with identity, current world state and the requested scene.")
        prompt="\n\n".join(parts); refs=[self.reference_path.read_bytes()] if self_present and self.reference_path.is_file() else []
        if self.debug: log.info("image_prompt kind=%s state=%s variant=%s reference_used=%s\n%s",kind,{"location":location,"activity":activity,"clothing":clothing},variant,bool(refs),prompt)
        return prompt,{"location":location,"activity":activity,"clothing":clothing,"kind":kind},refs
    async def allowed(self,chat_id,limit,cooldown):
        daily=await self.db.fetchone("SELECT count(*) n FROM image_generation_usage WHERE chat_id=? AND status='sent' AND date(created_at)=date('now')",(chat_id,)); recent=await self.db.fetchone("SELECT (julianday('now')-julianday(max(created_at)))*24 h FROM image_generation_usage WHERE chat_id=? AND status='sent'",(chat_id,))
        return daily["n"]<limit and (recent["h"] is None or recent["h"]>=cooldown)
