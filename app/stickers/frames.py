import asyncio
import hashlib
import io
import logging
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

from app.llm.schemas import ImageContent

log = logging.getLogger(__name__)


class StickerMediaValidationError(ValueError):
    """The downloaded bytes are not a supported Telegram sticker container."""


def detect_sticker_type(data: bytes) -> str | None:
    """Identify actual Telegram sticker bytes; file names and API flags are hints only."""
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "static"
    # EBML header: Telegram video stickers are WebM.
    if data.startswith(b"\x1a\x45\xdf\xa3"):
        return "video"
    # Telegram TGS stickers are gzip-compressed Lottie JSON.  Checking the
    # gzip header here is enough to route it to the real TGS parser below.
    if data.startswith(b"\x1f\x8b"):
        return "animated"
    return None


class StickerFrameExtractor:
    """Renders static/TGS/WEBM stickers to a bounded sequence of PNG frames."""
    def __init__(self, max_frames: int = 5):
        self.max_frames = max(1, min(max_frames, 5))

    @staticmethod
    def _png(data: bytes) -> bytes:
        image = Image.open(io.BytesIO(data)).convert("RGBA")
        result = io.BytesIO(); image.save(result, format="PNG")
        return result.getvalue()

    def extract(self, data: bytes, sticker_type: str) -> list[ImageContent]:
        if sticker_type == "static":
            return [ImageContent(data=self._png(data), mime_type="image/png", source="sticker_frame")]
        if sticker_type == "animated":
            return self._extract_tgs(data)
        if sticker_type == "video":
            return self._extract_video(data)
        raise ValueError(f"Unsupported sticker type: {sticker_type}")

    def _extract_tgs(self, data: bytes) -> list[ImageContent]:
        # python-lottie parses Telegram's gzip Lottie format; ffmpeg rasterizes
        # its SVG frames, avoiding a native renderer dependency.
        from lottie.parsers.tgs import parse_tgs
        from lottie.exporters import export_svg
        animation = parse_tgs(io.BytesIO(data))
        end = max(0, int(getattr(animation, "out_point", 1)) - 1)
        frames = sorted({round(end * ratio) for ratio in ([0] if self.max_frames == 1 else [index / (self.max_frames - 1) for index in range(self.max_frames)])})
        result = []
        with tempfile.TemporaryDirectory(prefix="companion-tgs-") as directory:
            base = Path(directory)
            for index, frame in enumerate(frames):
                svg, png = base / f"{index}.svg", base / f"{index}.png"
                with svg.open("w", encoding="utf-8") as handle:
                    export_svg(animation, handle, frame=frame)
                self._ffmpeg_frame(svg, png)
                result.append(ImageContent(data=png.read_bytes(), mime_type="image/png", source="sticker_tgs_frame"))
        return self._dedupe(result)

    def _extract_video(self, data: bytes) -> list[ImageContent]:
        with tempfile.TemporaryDirectory(prefix="companion-webm-") as directory:
            base, input_path = Path(directory), Path(directory) / "sticker.webm"
            input_path.write_bytes(data)
            probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(input_path)], capture_output=True, text=True, check=True, timeout=10)
            duration = max(.01, float(probe.stdout.strip() or 0))
            moments = [duration * index / max(1, self.max_frames - 1) for index in range(self.max_frames)]
            frames = []
            for index, moment in enumerate(moments):
                output = base / f"frame-{index:02d}.png"
                subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{moment:.3f}", "-i", str(input_path), "-frames:v", "1", str(output)], check=True, timeout=20)
                if output.exists(): frames.append(ImageContent(data=output.read_bytes(), mime_type="image/png", source="sticker_video_frame"))
        return self._dedupe(frames)

    @staticmethod
    def _ffmpeg_frame(svg: Path, png: Path) -> None:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(svg), "-frames:v", "1", str(png)], check=True, timeout=20)

    @staticmethod
    def _dedupe(frames: list[ImageContent]) -> list[ImageContent]:
        result, hashes = [], set()
        for frame in frames:
            digest = hashlib.sha256(frame.data).digest()
            if digest not in hashes:
                hashes.add(digest); result.append(frame)
        return result
