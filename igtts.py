"""Gemini TTS 工具集 helper utilities."""
from __future__ import annotations

import json
import logging
import time
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from google import genai
from google.genai import types

__all__ = [
    "__version__",
    "CONFIG_PATH",
    "DEFAULT_CONFIG",
    "DEFAULT_VOICES",
    "configure_logging",
    "load_config",
    "save_config",
    "fetch_available_voices",
    "translate_voice_label",
    "create_client",
    "gemini_tts",
    "save_as_wav_file",
]

__version__ = "1.4.5"

CONFIG_FILENAME = "config.json"


def _config_path() -> Path:
    """Return the path to the runtime configuration file."""
    return Path.cwd() / CONFIG_FILENAME


CONFIG_PATH = _config_path()
MODEL_KEY = "model"
DEFAULT_MODEL = "gemini-2.5-pro-preview-tts"
DEFAULT_LOG_FILE = "log.log"
VOICE_ENDPOINT = "https://texttospeech.googleapis.com/v1/voices"
VOICE_CACHE_TTL_SECONDS = 24 * 60 * 60

WAV_CHANNELS = 1
WAV_RATE = 24_000
WAV_SAMPLE_WIDTH = 2

LOGGER = logging.getLogger("igtts")
LOGGER.addHandler(logging.NullHandler())

KNOWN_CONFIG_KEYS = {
    "api_key",
    "base_url",
    MODEL_KEY,
    "default_voice",
    "default_output",
    "input_text_path",
    "voices",
    "voices_cached_at",
    "debug_enabled",
    "log_file",
    "batch_tasks_path",
    "multi_delay_seconds",
    "version",
}

_DEFAULT_VOICE_DATA: List[Dict[str, str]] = [
    {"id": "Zephyr", "label": "Zephyr (明亮)"},
    {"id": "Puck", "label": "Puck (欢快)"},
    {"id": "Charon", "label": "Charon (信息丰富)"},
    {"id": "Kore", "label": "Kore (坚定)"},
    {"id": "Fenrir", "label": "Fenrir (易激动)"},
    {"id": "Leda", "label": "Leda (年轻)"},
    {"id": "Orus", "label": "Orus (坚定)"},
    {"id": "Aoede", "label": "Aoede (轻松)"},
    {"id": "Callirrhoe", "label": "Callirrhoe (随和)"},
    {"id": "Autonoe", "label": "Autonoe (明亮)"},
    {"id": "Enceladus", "label": "Enceladus (呼吸感)"},
    {"id": "Iapetus", "label": "Iapetus (清晰)"},
    {"id": "Umbriel", "label": "Umbriel (随和)"},
    {"id": "Algieba", "label": "Algieba (平滑)"},
    {"id": "Despina", "label": "Despina (平滑)"},
    {"id": "Erinome", "label": "Erinome (清晰)"},
    {"id": "Algenib", "label": "Algenib (沙哑)"},
    {"id": "Rasalgethi", "label": "Rasalgethi (信息丰富)"},
    {"id": "Laomedeia", "label": "Laomedeia (欢快)"},
    {"id": "Achernar", "label": "Achernar (轻柔)"},
    {"id": "Alnilam", "label": "Alnilam (坚定)"},
    {"id": "Schedar", "label": "Schedar (平稳)"},
    {"id": "Gacrux", "label": "Gacrux (成熟)"},
    {"id": "Pulcherrima", "label": "Pulcherrima (向前)"},
    {"id": "Achird", "label": "Achird (友好)"},
    {"id": "Zubenelgenubi", "label": "Zubenelgenubi (休闲)"},
    {"id": "Vindemiatrix", "label": "Vindemiatrix (温柔)"},
    {"id": "Sadachbia", "label": "Sadachbia (活泼)"},
    {"id": "Sadaltager", "label": "Sadaltager (博学)"},
    {"id": "Sulafat", "label": "Sulafat (温暖)"},
]


def _default_voice_list() -> List[Dict[str, str]]:
    """Return a fresh copy of the built-in voice list."""
    return [voice.copy() for voice in _DEFAULT_VOICE_DATA]


def _clone_default_config() -> Dict[str, Any]:
    """Return a mutable clone of the default configuration."""
    return {
        "api_key": "",
        "base_url": "https://generativelanguage.googleapis.com",
        MODEL_KEY: DEFAULT_MODEL,
        "default_voice": "Zephyr",
        "default_output": "output.wav",
        "input_text_path": "input.txt",
        "voices": _default_voice_list(),
        "voices_cached_at": None,
        "debug_enabled": False,
        "log_file": DEFAULT_LOG_FILE,
        "batch_tasks_path": "",
        "multi_delay_seconds": 0.0,
        "version": __version__,
    }


DEFAULT_VOICES: List[Dict[str, str]] = _default_voice_list()
DEFAULT_CONFIG: Dict[str, Any] = _clone_default_config()


def configure_logging(config: Dict[str, Any]) -> None:
    """Set up logging destinations according to configuration."""
    level = logging.DEBUG if config.get("debug_enabled") else logging.INFO
    LOGGER.setLevel(level)
    LOGGER.propagate = False

    console_handler = next(
        (handler for handler in LOGGER.handlers if isinstance(handler, logging.StreamHandler)),
        None,
    )
    if console_handler is None:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        LOGGER.addHandler(console_handler)
    console_handler.setLevel(logging.DEBUG if config.get("debug_enabled") else logging.WARNING)

    for handler in list(LOGGER.handlers):
        if isinstance(handler, logging.FileHandler):
            LOGGER.removeHandler(handler)
            try:
                handler.close()
            except Exception as exc:  # pragma: no cover
                LOGGER.warning("Failed to close previous log file handle: %s", exc)

    if config.get("debug_enabled") and config.get("log_file"):
        log_path = Path(str(config["log_file"]))
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(log_path, encoding="utf-8")
        except OSError as exc:
            LOGGER.warning("Unable to open log file %s: %s", log_path, exc)
        else:
            file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
            file_handler.setLevel(logging.DEBUG)
            LOGGER.addHandler(file_handler)


def load_config() -> Dict[str, Any]:
    """Read configuration from disk, creating defaults when absent."""
    path = _config_path()
    globals()["CONFIG_PATH"] = path

    if not path.exists():
        defaults = _clone_default_config()
        save_config(defaults)
        return defaults

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        LOGGER.warning("Configuration file corrupted, restoring defaults.")
        defaults = _clone_default_config()
        save_config(defaults)
        return defaults

    return _ensure_config_schema(data)


def save_config(config: Dict[str, Any]) -> None:
    """Persist configuration to disk."""
    path = _config_path()
    globals()["CONFIG_PATH"] = path

    try:
        path.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        LOGGER.warning("Failed to write configuration: %s", exc)


def _ensure_config_schema(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Merge defaults and remove unknown keys."""
    merged: Dict[str, Any] = _clone_default_config()
    if config:
        for key, value in config.items():
            if key in KNOWN_CONFIG_KEYS:
                merged[key] = value

    voices = merged.get("voices") or []
    if not isinstance(voices, list) or not voices:
        voices = _default_voice_list()
    else:
        normalized: List[Dict[str, str]] = []
        for item in voices:
            if not isinstance(item, dict):
                continue
            voice_id = str(item.get("id", "")).strip()
            if not voice_id:
                continue
            label = str(item.get("label", "")).strip() or voice_id
            normalized.append({"id": voice_id, "label": label})
        voices = normalized or _default_voice_list()
    merged["voices"] = voices

    cached_at = merged.get("voices_cached_at")
    if not isinstance(cached_at, (int, float)):
        merged["voices_cached_at"] = None

    if not merged.get("log_file"):
        merged["log_file"] = DEFAULT_LOG_FILE

    try:
        merged["multi_delay_seconds"] = float(merged.get("multi_delay_seconds", 0.0))
    except (TypeError, ValueError):
        merged["multi_delay_seconds"] = 0.0

    merged["version"] = __version__

    return merged


def fetch_available_voices(
    config: Optional[Dict[str, Any]] = None,
    *,
    force_refresh: bool = False,
) -> List[Dict[str, str]]:
    """Retrieve available voices from Gemini API or fall back to defaults."""
    config = _ensure_config_schema(config or load_config())
    cached_voices = config.get("voices") or []
    cached_at = config.get("voices_cached_at")
    default_voices = _default_voice_list()

    if (
        cached_voices
        and cached_at
        and not force_refresh
        and (time.time() - float(cached_at)) < VOICE_CACHE_TTL_SECONDS
    ):
        return cached_voices

    api_key = str(config.get("api_key", "")).strip()
    if not api_key:
        if not cached_voices:
            config["voices"] = default_voices
            config["voices_cached_at"] = time.time()
            save_config(config)
        return cached_voices or default_voices

    try:
        response = requests.get(VOICE_ENDPOINT, params={"key": api_key}, timeout=30)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        LOGGER.warning("Failed to fetch voice list: %s", exc)
        if not cached_voices:
            config["voices"] = default_voices
            config["voices_cached_at"] = time.time()
            save_config(config)
        return cached_voices or default_voices
    except json.JSONDecodeError as exc:
        LOGGER.warning("Voice list response invalid JSON: %s", exc)
        if not cached_voices:
            config["voices"] = default_voices
            config["voices_cached_at"] = time.time()
            save_config(config)
        return cached_voices or default_voices

    voices: List[Dict[str, str]] = []
    for item in payload.get("voices", []):
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not name:
            continue
        description = item.get("description")
        language_codes = item.get("languageCodes") if isinstance(item.get("languageCodes"), list) else None
        voices.append(
            {
                "id": name,
                "label": translate_voice_label(name, description, language_codes=language_codes),
            }
        )

    if not voices:
        LOGGER.warning("Voice list response empty, using defaults.")
        voices = cached_voices or default_voices

    config["voices"] = voices
    config["voices_cached_at"] = time.time()
    save_config(config)

    return voices


def translate_voice_label(
    voice_id: str,
    description: Optional[str] = None,
    *,
    language_codes: Optional[List[str]] = None,
) -> str:
    """Generate a human-friendly label for a voice."""
    detail_parts: List[str] = []
    if description:
        detail_parts.append(description)
    if language_codes:
        detail_parts.extend(language_codes)
    detail_text = ", ".join(part for part in detail_parts if part).strip()
    if detail_text and detail_text.lower() not in voice_id.lower():
        return f"{voice_id} ({detail_text})"
    return voice_id


def create_client(config: Dict[str, Any]) -> genai.Client:
    """Create a Gemini client using the provided configuration."""
    api_key = str(config.get("api_key", "")).strip()
    if not api_key:
        raise ValueError("Gemini API key missing. Please configure it first.")

    base_url = str(config.get("base_url", "")).strip()
    http_options = types.HttpOptions(base_url=base_url) if base_url else None
    return genai.Client(api_key=api_key, http_options=http_options)


def gemini_tts(
    text: str,
    voice: str,
    output_path: str,
    *,
    speed: float = 1.0,
    config: Optional[Dict[str, Any]] = None,
) -> bool:
    """Generate speech for *text* using *voice* and save to *output_path*."""
    if not text or not text.strip():
        raise ValueError("Input text is empty.")

    target_config = _ensure_config_schema(config or load_config())
    client = create_client(target_config)

    speech_config = types.SpeechConfig(
        voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice)
        )
    )

    LOGGER.info("Requesting speech synthesis for voice=%s -> %s", voice, output_path)
    response = client.models.generate_content(
        model=target_config.get(MODEL_KEY, DEFAULT_MODEL),
        contents=text,
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=speech_config,
        ),
    )

    pcm_data = _extract_pcm_data(response)
    if pcm_data is None:
        raise RuntimeError("Gemini response did not include audio content.")

    save_as_wav_file(output_path, pcm_data, _calculate_sample_rate(speed))
    LOGGER.info("Audio saved to %s", output_path)
    return True


def _extract_pcm_data(response: types.GenerateContentResponse) -> Optional[bytes]:
    """Extract inline PCM payload from response."""
    if not response or not getattr(response, "candidates", None):
        LOGGER.error("Gemini response is empty.")
        return None

    candidate = response.candidates[0]
    parts = getattr(candidate.content, "parts", []) if candidate and candidate.content else []
    for part in parts:
        inline_data = getattr(part, "inline_data", None)
        if inline_data and getattr(inline_data, "data", None):
            return inline_data.data

    LOGGER.error("No PCM payload found in Gemini response.")
    return None


def _calculate_sample_rate(speed: float) -> int:
    """Clamp speed and calculate sample rate."""
    try:
        value = float(speed)
    except (TypeError, ValueError):
        value = 1.0
    value = max(0.5, min(value, 2.0))
    return int(WAV_RATE * value)


def save_as_wav_file(filename: str, pcm_data: bytes, sample_rate: int = WAV_RATE) -> None:
    """Persist PCM bytes to WAV file."""
    target = Path(filename)
    target.parent.mkdir(parents=True, exist_ok=True)

    wav_file: Optional[wave.Wave_write] = None
    try:
        wav_file = wave.open(str(target), "wb")
        wav_file.setnchannels(WAV_CHANNELS)
        wav_file.setsampwidth(WAV_SAMPLE_WIDTH)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm_data)
    finally:
        if wav_file is not None:
            try:
                wav_file.close()
            except Exception as exc:  # pragma: no cover
                LOGGER.warning("Failed to close WAV handle for %s: %s", target, exc)
