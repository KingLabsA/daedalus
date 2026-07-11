"""Vision — image and video understanding via any OpenAI-compatible vision model."""

import base64
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp"}
MAX_IMAGE_BYTES = 20_000_000


def image_to_data_url(path: str) -> str:
    p = Path(path)
    mime = MIME.get(p.suffix.lower())
    if not mime:
        raise ValueError(f"Unsupported image type: {p.suffix} (supported: {', '.join(MIME)})")
    data = p.read_bytes()
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError(f"Image too large: {len(data)} bytes")
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def _ffmpeg_extract_frames(video_path: str, out_dir: str, max_frames: int) -> list[str]:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not installed (brew install ffmpeg)")
    pattern = str(Path(out_dir) / "frame_%04d.jpg")
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", video_path, "-vf", "fps=1", "-q:v", "4", pattern],
        check=True,
        timeout=300,
        capture_output=True,
    )
    frames = sorted(str(f) for f in Path(out_dir).glob("frame_*.jpg"))
    if len(frames) <= max_frames:
        return frames
    step = len(frames) / max_frames
    return [frames[int(i * step)] for i in range(max_frames)]


class Vision:
    def __init__(self, vision_chat_fn: Callable[[list], str], frame_extractor: Callable | None = None):
        """vision_chat_fn(messages) -> str, where messages use OpenAI content-part format."""
        self.vision_chat_fn = vision_chat_fn
        self.frame_extractor = frame_extractor or _ffmpeg_extract_frames

    @staticmethod
    def _image_message(question: str, image_paths: list[str]) -> list:
        parts = [{"type": "text", "text": question}]
        for path in image_paths:
            parts.append({"type": "image_url", "image_url": {"url": image_to_data_url(path)}})
        return [{"role": "user", "content": parts}]

    def analyze_image(self, path: str, question: str = "Describe this image in detail.") -> str:
        if not Path(path).exists():
            return f"Image not found: {path}"
        try:
            return str(self.vision_chat_fn(self._image_message(question, [path])))
        except ValueError as exc:
            return str(exc)
        except Exception as exc:
            return f"Vision call failed: {exc}"

    def analyze_video(self, path: str, question: str = "What happens in this video?", max_frames: int = 6) -> str:
        if not Path(path).exists():
            return f"Video not found: {path}"
        try:
            with tempfile.TemporaryDirectory(prefix="hermes_video_") as tmp:
                frames = self.frame_extractor(path, tmp, max_frames)
                if not frames:
                    return "No frames could be extracted."
                descriptions = []
                for i, frame in enumerate(frames, 1):
                    desc = str(self.vision_chat_fn(self._image_message(f"Frame {i}/{len(frames)} of a video. Describe what is happening.", [frame])))
                    descriptions.append(f"Frame {i}: {desc}")
                summary_prompt = f"These are sequential frame descriptions from one video. Answer: {question}\n\n" + "\n".join(descriptions)
                return str(self.vision_chat_fn([{"role": "user", "content": summary_prompt}]))
        except Exception as exc:
            return f"Video analysis failed: {exc}"
