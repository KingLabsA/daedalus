"""VoiceIO — speech in (record + transcribe) and speech out (TTS + play).

All network calls injected; recording/playback via sox or ffmpeg/afplay when present.
Every failure degrades to a helpful string — never an exception.
"""

import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path


class VoiceIO:
    def __init__(
        self,
        transcribe_fn: Callable[[str], str] | None = None,
        tts_fn: Callable[[str], bytes] | None = None,
    ):
        self.transcribe_fn = transcribe_fn
        self.tts_fn = tts_fn

    # ── Speech in ─────────────────────────────────────────────
    def transcribe(self, audio_path: str) -> str:
        if not self.transcribe_fn:
            return "ASR not configured (set an OpenAI-compatible provider; HERMES_ASR_PROVIDER/HERMES_ASR_MODEL)."
        if not Path(audio_path).exists():
            return f"Audio file not found: {audio_path}"
        try:
            return str(self.transcribe_fn(audio_path))
        except Exception as exc:
            return f"Transcription failed: {exc}"

    @staticmethod
    def _record_cmd(out_path: str, seconds: int):
        if shutil.which("rec"):  # sox's recording alias
            return ["rec", out_path, "trim", "0", str(seconds)]
        if shutil.which("sox"):
            return ["sox", "-d", out_path, "trim", "0", str(seconds)]
        if shutil.which("ffmpeg"):
            return ["ffmpeg", "-y", "-loglevel", "error", "-f", "avfoundation", "-i", ":0", "-t", str(seconds), out_path]
        return None

    def listen(self, seconds: int = 5) -> str:
        cmd_probe = self._record_cmd("/tmp/probe.wav", seconds)
        if not cmd_probe:
            return "No recorder available: install sox (brew install sox) or ffmpeg."
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                out_path = tmp.name
            cmd = self._record_cmd(out_path, seconds)
            subprocess.run(cmd, check=True, timeout=seconds + 15, capture_output=True)
            text = self.transcribe(out_path)
            Path(out_path).unlink(missing_ok=True)
            return text
        except subprocess.CalledProcessError as exc:
            return f"Recording failed: {exc.stderr.decode(errors='replace')[:300] if exc.stderr else exc}"
        except Exception as exc:
            return f"Listen failed: {exc}"

    # ── Speech out ────────────────────────────────────────────
    def speak(self, text: str) -> str:
        if not self.tts_fn:
            return "TTS not configured (HERMES_TTS_MODEL / provider with audio.speech support)."
        try:
            audio = self.tts_fn(text)
        except Exception as exc:
            return f"TTS failed: {exc}"
        try:
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
                tmp.write(audio)
                path = tmp.name
            player = "afplay" if shutil.which("afplay") else ("ffplay" if shutil.which("ffplay") else None)
            if not player:
                return f"Audio saved to {path} (no player found: afplay/ffplay)"
            args = [player, path] if player == "afplay" else [player, "-nodisp", "-autoexit", "-loglevel", "quiet", path]
            subprocess.run(args, timeout=120, capture_output=True)
            Path(path).unlink(missing_ok=True)
            return f"Spoke {len(text)} chars."
        except Exception as exc:
            return f"Playback failed: {exc}"
