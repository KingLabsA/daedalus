"""Tests for core.senses — model orchestra (MoE), vision, voice. All offline with fakes."""

import base64
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.senses import ModelOrchestra, Vision, VoiceIO
from core.senses.vision import image_to_data_url

# 1x1 transparent PNG
PNG_BYTES = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


def test_senses_no_agent_ultimate_dependency():
    import core.senses as senses

    for mod_file in Path(senses.__file__).parent.glob("*.py"):
        assert not re.search(r"^\s*(?:from|import)\s+agent_ultimate", mod_file.read_text(), re.M), mod_file.name


# ── ModelOrchestra ───────────────────────────────────────────


def _orchestra(available, answers=None):
    answers = answers or {}
    return ModelOrchestra(
        call_fn=lambda provider, prompt: answers.get(provider, f"{provider}-answer"),
        available_fn=lambda: available,
    )


def test_classify_task_types():
    orch = _orchestra(["openai"])
    assert orch.classify("fix this bug in the function and add a test") == "code"
    assert orch.classify("what is the latest news today") == "search"
    assert orch.classify("write a poem, be creative") == "creative"
    assert orch.classify("why should we choose this architecture? explain the trade-offs") == "reasoning"


def test_pick_respects_profile_order_and_availability():
    orch = _orchestra(["groq", "deepseek"])
    assert orch.pick("code") == "deepseek"  # first available in code profile
    assert orch.pick("cheap") == "groq"
    # profile has no available member -> falls back to first available
    orch2 = _orchestra(["novita"])
    assert orch2.pick("vision") == "novita"


def test_pick_no_providers():
    assert _orchestra([]).pick("code") is None
    result = _orchestra([]).consult("hello")
    assert result["provider"] is None


def test_consult_routes_and_survives_failure():
    orch = _orchestra(["deepseek"], answers={"deepseek": "the fix is X"})
    result = orch.consult("fix this bug in the code")
    assert result == {"task_type": "code", "provider": "deepseek", "answer": "the fix is X"}

    def boom(provider, prompt):
        raise RuntimeError("api down")

    failing = ModelOrchestra(call_fn=boom, available_fn=lambda: ["openai"])
    result = failing.consult("why?")
    assert "failed" in result["answer"]


def test_committee_fans_out_and_synthesizes():
    calls = []

    def call_fn(provider, prompt):
        calls.append(provider)
        if "Synthesize" in prompt:
            return "SYNTHESIS"
        return f"{provider}-view"

    orch = ModelOrchestra(call_fn=call_fn, available_fn=lambda: ["openai", "deepseek", "mistral"])
    result = orch.committee("why is the build slow?", n=3)
    assert len(result["experts"]) == 3
    assert result["synthesis"] == "SYNTHESIS"
    assert result["synthesizer"] == "openai"  # reasoning profile head


def test_committee_single_survivor_skips_synthesis():
    def call_fn(provider, prompt):
        if provider != "openai":
            raise RuntimeError("down")
        return "only-answer"

    orch = ModelOrchestra(call_fn=call_fn, available_fn=lambda: ["openai", "deepseek"])
    result = orch.committee("question", n=2)
    assert result["synthesis"] == "only-answer"


# ── Vision ───────────────────────────────────────────────────


def test_image_to_data_url(tmp_path):
    img = tmp_path / "pic.png"
    img.write_bytes(PNG_BYTES)
    url = image_to_data_url(str(img))
    assert url.startswith("data:image/png;base64,")
    with pytest.raises(ValueError):
        bad = tmp_path / "doc.xyz"
        bad.write_bytes(b"nope")
        image_to_data_url(str(bad))


def test_analyze_image_message_shape(tmp_path):
    img = tmp_path / "pic.png"
    img.write_bytes(PNG_BYTES)
    captured = {}

    def chat_fn(messages):
        captured["messages"] = messages
        return "a tiny pixel"

    v = Vision(vision_chat_fn=chat_fn)
    out = v.analyze_image(str(img), "what is this?")
    assert out == "a tiny pixel"
    parts = captured["messages"][0]["content"]
    assert parts[0] == {"type": "text", "text": "what is this?"}
    assert parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_analyze_image_missing_file():
    v = Vision(vision_chat_fn=lambda m: "x")
    assert "not found" in v.analyze_image("/nope/missing.png")


def test_analyze_video_samples_frames_and_synthesizes(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake video")
    frames_dir_used = {}

    def extractor(path, out_dir, max_frames):
        frames_dir_used["dir"] = out_dir
        frames = []
        for i in range(3):
            frame = Path(out_dir) / f"frame_{i}.png"
            frame.write_bytes(PNG_BYTES)
            frames.append(str(frame))
        return frames

    prompts = []

    def chat_fn(messages):
        content = messages[0]["content"]
        prompts.append(content)
        if isinstance(content, str):  # synthesis call
            return "VIDEO SUMMARY"
        return "frame desc"

    v = Vision(vision_chat_fn=chat_fn, frame_extractor=extractor)
    out = v.analyze_video(str(video), "what happens?")
    assert out == "VIDEO SUMMARY"
    assert len(prompts) == 4  # 3 frames + 1 synthesis
    assert "what happens?" in prompts[-1]


def test_analyze_video_extractor_failure_is_graceful(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake")

    def extractor(path, out_dir, max_frames):
        raise RuntimeError("ffmpeg not installed")

    v = Vision(vision_chat_fn=lambda m: "x", frame_extractor=extractor)
    assert "ffmpeg not installed" in v.analyze_video(str(video))


# ── VoiceIO ──────────────────────────────────────────────────


def test_transcribe_paths(tmp_path):
    unconfigured = VoiceIO()
    assert "not configured" in unconfigured.transcribe("x.wav")
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF")
    v = VoiceIO(transcribe_fn=lambda p: "hello world")
    assert v.transcribe(str(audio)) == "hello world"
    assert "not found" in v.transcribe("/nope/b.wav")

    def boom(p):
        raise RuntimeError("asr down")

    assert "asr down" in VoiceIO(transcribe_fn=boom).transcribe(str(audio))


def test_speak_paths(monkeypatch):
    unconfigured = VoiceIO()
    assert "not configured" in unconfigured.speak("hi")

    def boom(text):
        raise RuntimeError("tts down")

    assert "tts down" in VoiceIO(tts_fn=boom).speak("hi")
    # no player found -> reports saved path instead of crashing
    monkeypatch.setattr("shutil.which", lambda name: None)
    v = VoiceIO(tts_fn=lambda t: b"mp3bytes")
    out = v.speak("hello")
    assert "Audio saved to" in out


def test_listen_without_recorder(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    v = VoiceIO(transcribe_fn=lambda p: "hi")
    assert "No recorder available" in v.listen(1)
