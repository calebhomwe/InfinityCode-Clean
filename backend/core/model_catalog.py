"""Model catalog — every model the app can reach, grouped by family.

Plain data only (no app imports) so router.py and the /models/catalog endpoint
can share it without import cycles. The DashScope entries are the owner's
Model Studio FREE-quota inventory (verified 2026-08-09); chat models are priced
0 so budget guardrails never block free calls — when a quota exhausts the
provider errors and the chain walker degrades (Stop-on-Exhaust).

Fields:
  id             bare DashScope model id (registered as dashscope/<id>)
  family         grouping shown in the Models UI
  context        context window (tokens) — approximate for budgeting only
  vision         supports image input (vl / omni / ocr / qvq)
  note           one-line human description
"""

from __future__ import annotations

from typing import Any, Dict, List

# --------------------------------------------------------------------------- #
# Qwen chat-capable models (DashScope free quota). Realtime-only, ASR/TTS and
# video models are NOT here — they need dedicated (non chat-completions) APIs.
# --------------------------------------------------------------------------- #
QWEN_CHAT_MODELS: List[Dict[str, Any]] = [
    # --- omni (multimodal chat, vision + audio in) ---
    {"id": "qwen3.5-omni-plus", "family": "omni", "context": 131_072, "vision": True,
     "note": "omni flagship (2026-03-15 alias also free)"},
    {"id": "qwen3.5-omni-plus-2026-03-15", "family": "omni", "context": 131_072, "vision": True,
     "note": "omni flagship dated snapshot"},
    {"id": "qwen3.5-omni-flash", "family": "omni", "context": 131_072, "vision": True,
     "note": "omni fast tier"},
    {"id": "qwen3.5-omni-flash-2026-03-15", "family": "omni", "context": 131_072, "vision": True,
     "note": "omni fast dated snapshot"},
    {"id": "qwen3-omni-flash", "family": "omni", "context": 131_072, "vision": True,
     "note": "qwen3 omni fast"},
    {"id": "qwen3-omni-flash-2025-09-15", "family": "omni", "context": 131_072, "vision": True,
     "note": "qwen3 omni fast dated"},
    {"id": "qwen3-omni-flash-2025-12-01", "family": "omni", "context": 131_072, "vision": True,
     "note": "qwen3 omni fast dated"},
    {"id": "qwen-omni-turbo", "family": "omni", "context": 131_072, "vision": True,
     "note": "omni turbo (2025-03-26 alias also free)"},
    {"id": "qwen2.5-omni-7b", "family": "omni", "context": 131_072, "vision": True,
     "note": "open omni 7B"},

    # --- vision (vl / ocr / qvq) ---
    {"id": "qwen-vl-ocr", "family": "vision", "context": 131_072, "vision": True,
     "note": "document / screenshot OCR"},
    {"id": "qwen-vl-ocr-2025-11-20", "family": "vision", "context": 131_072, "vision": True,
     "note": "OCR dated snapshot"},
    {"id": "qwen3-vl-235b-a22b-thinking", "family": "vision", "context": 131_072, "vision": True,
     "note": "largest VL, thinking"},
    {"id": "qwen3-vl-235b-a22b-instruct", "family": "vision", "context": 131_072, "vision": True,
     "note": "largest VL, instruct"},
    {"id": "qwen3-vl-32b-thinking", "family": "vision", "context": 131_072, "vision": True,
     "note": "VL 32B thinking"},
    {"id": "qwen3-vl-32b-instruct", "family": "vision", "context": 131_072, "vision": True,
     "note": "VL 32B instruct"},
    {"id": "qwen3-vl-30b-a3b-thinking", "family": "vision", "context": 131_072, "vision": True,
     "note": "VL MoE thinking"},
    {"id": "qwen3-vl-30b-a3b-instruct", "family": "vision", "context": 131_072, "vision": True,
     "note": "VL MoE instruct"},
    {"id": "qwen3-vl-8b-thinking", "family": "vision", "context": 131_072, "vision": True,
     "note": "VL 8B thinking"},
    {"id": "qwen3-vl-8b-instruct", "family": "vision", "context": 131_072, "vision": True,
     "note": "VL 8B instruct"},
    {"id": "qwen3-vl-plus", "family": "vision", "context": 131_072, "vision": True,
     "note": "VL plus (2025-09-23 / 2025-12-19 aliases also free)"},
    {"id": "qwen3-vl-plus-2025-09-23", "family": "vision", "context": 131_072, "vision": True,
     "note": "VL plus dated"},
    {"id": "qwen3-vl-plus-2025-12-19", "family": "vision", "context": 131_072, "vision": True,
     "note": "VL plus dated"},
    {"id": "qwen3-vl-flash", "family": "vision", "context": 131_072, "vision": True,
     "note": "VL fast (2025-10-15 / 2026-01-22 aliases also free)"},
    {"id": "qwen3-vl-flash-2025-10-15", "family": "vision", "context": 131_072, "vision": True,
     "note": "VL flash dated"},
    {"id": "qwen3-vl-flash-2026-01-22", "family": "vision", "context": 131_072, "vision": True,
     "note": "VL flash dated"},
    {"id": "qwen-vl-plus", "family": "vision", "context": 131_072, "vision": True,
     "note": "classic VL plus"},
    {"id": "qwen-vl-max", "family": "vision", "context": 131_072, "vision": True,
     "note": "classic VL max"},
    {"id": "qvq-max", "family": "vision", "context": 262_144, "vision": True,
     "note": "visual reasoning"},

    # --- coder ---
    {"id": "qwen3-coder-480b-a35b-instruct", "family": "coder", "context": 262_144, "vision": False,
     "note": "flagship coder (already the longtask builder)"},
    {"id": "qwen3-coder-next", "family": "coder", "context": 262_144, "vision": False,
     "note": "next-gen coder"},
    {"id": "qwen3-coder-plus-2025-07-22", "family": "coder", "context": 262_144, "vision": False,
     "note": "coder plus dated"},
    {"id": "qwen3-coder-plus-2025-09-23", "family": "coder", "context": 262_144, "vision": False,
     "note": "coder plus dated"},
    {"id": "qwen3-coder-30b-a3b-instruct", "family": "coder", "context": 262_144, "vision": False,
     "note": "coder MoE 30B"},
    {"id": "qwen3-coder-flash-2025-07-28", "family": "coder", "context": 262_144, "vision": False,
     "note": "coder flash dated"},

    # --- open qwen3 family ---
    {"id": "qwen3-235b-a22b-thinking-2507", "family": "qwen3", "context": 262_144, "vision": False,
     "note": "235B MoE thinking"},
    {"id": "qwen3-235b-a22b-instruct-2507", "family": "qwen3", "context": 262_144, "vision": False,
     "note": "235B MoE instruct"},
    {"id": "qwen3-next-80b-a3b-thinking", "family": "qwen3", "context": 262_144, "vision": False,
     "note": "80B MoE thinking"},
    {"id": "qwen3-next-80b-a3b-instruct", "family": "qwen3", "context": 262_144, "vision": False,
     "note": "80B MoE instruct"},
    {"id": "qwen3-32b", "family": "qwen3", "context": 262_144, "vision": False,
     "note": "dense 32B"},
    {"id": "qwen3-30b-a3b-thinking-2507", "family": "qwen3", "context": 262_144, "vision": False,
     "note": "30B MoE thinking"},
    {"id": "qwen3-30b-a3b-instruct-2507", "family": "qwen3", "context": 262_144, "vision": False,
     "note": "30B MoE instruct"},
    {"id": "qwen3-30b-a3b", "family": "qwen3", "context": 262_144, "vision": False,
     "note": "30B MoE"},
    {"id": "qwen3.5-35b-a3b", "family": "qwen3", "context": 262_144, "vision": False,
     "note": "3.5 MoE 35B"},
    {"id": "qwen3.6-35b-a3b", "family": "qwen3", "context": 262_144, "vision": False,
     "note": "3.6 MoE 35B"},
    {"id": "qwen3.5-27b", "family": "qwen3", "context": 262_144, "vision": False,
     "note": "3.5 dense 27B"},
    {"id": "qwen3-14b", "family": "qwen3", "context": 262_144, "vision": False,
     "note": "dense 14B"},
    {"id": "qwen3-8b", "family": "qwen3", "context": 262_144, "vision": False,
     "note": "dense 8B"},

    # --- max ---
    {"id": "qwen-max", "family": "max", "context": 1_000_000, "vision": False,
     "note": "flagship (FREE on this account)"},
    {"id": "qwen3-max-2025-09-23", "family": "max", "context": 1_000_000, "vision": False,
     "note": "qwen3 max dated"},
    {"id": "qwen3-max-2026-01-23", "family": "max", "context": 1_000_000, "vision": False,
     "note": "qwen3 max dated"},

    # --- standard plus / flash / turbo ---
    {"id": "qwen-plus", "family": "standard", "context": 1_000_000, "vision": False,
     "note": "plus (latest + 4 dated aliases also free)"},
    {"id": "qwen-plus-latest", "family": "standard", "context": 1_000_000, "vision": False,
     "note": "plus latest alias"},
    {"id": "qwen-plus-2025-04-28", "family": "standard", "context": 1_000_000, "vision": False,
     "note": "plus dated"},
    {"id": "qwen-plus-2025-07-14", "family": "standard", "context": 1_000_000, "vision": False,
     "note": "plus dated"},
    {"id": "qwen-plus-2025-07-28", "family": "standard", "context": 1_000_000, "vision": False,
     "note": "plus dated"},
    {"id": "qwen-plus-2025-09-11", "family": "standard", "context": 1_000_000, "vision": False,
     "note": "plus dated"},
    {"id": "qwen3.5-plus-2026-02-15", "family": "standard", "context": 1_000_000, "vision": False,
     "note": "3.5 plus dated"},
    {"id": "qwen3.5-plus-2026-04-20", "family": "standard", "context": 1_000_000, "vision": False,
     "note": "3.5 plus dated"},
    {"id": "qwen-turbo", "family": "standard", "context": 1_000_000, "vision": False,
     "note": "turbo (FREE)"},
    {"id": "qwen3.5-flash", "family": "standard", "context": 1_000_000, "vision": False,
     "note": "3.5 flash (2026-02-23 alias also free)"},
    {"id": "qwen3.5-flash-2026-02-23", "family": "standard", "context": 1_000_000, "vision": False,
     "note": "3.5 flash dated"},
    {"id": "qwen3.6-flash-2026-04-16", "family": "standard", "context": 1_000_000, "vision": False,
     "note": "3.6 flash dated"},
    {"id": "qwen-flash-2025-07-28", "family": "standard", "context": 1_000_000, "vision": False,
     "note": "flash dated"},

    # --- reasoning ---
    {"id": "qwq-plus", "family": "reasoning", "context": 262_144, "vision": False,
     "note": "math / logic reasoning"},

    # --- translation ---
    {"id": "qwen-mt-flash", "family": "translation", "context": 131_072, "vision": False,
     "note": "machine translation fast"},
    {"id": "qwen-mt-lite", "family": "translation", "context": 131_072, "vision": False,
     "note": "machine translation lite"},
    {"id": "qwen-mt-plus", "family": "translation", "context": 131_072, "vision": False,
     "note": "machine translation plus"},
    {"id": "qwen-mt-turbo", "family": "translation", "context": 131_072, "vision": False,
     "note": "machine translation turbo"},

    # --- character ---
    {"id": "qwen-plus-character", "family": "character", "context": 131_072, "vision": False,
     "note": "roleplay persona"},
    {"id": "qwen-flash-character", "family": "character", "context": 131_072, "vision": False,
     "note": "roleplay persona fast"},

    # --- deepseek served by DashScope (free quota) ---
    {"id": "deepseek-v3.2", "family": "deepseek", "context": 131_072, "vision": False,
     "note": "DeepSeek v3.2 via Model Studio"},
]

# --------------------------------------------------------------------------- #
# Qwen audio models — dedicated DashScope APIs (ASR/TTS/realtime), not chat
# completions. Registered for the Models UI catalog; use via the audio tools.
# --------------------------------------------------------------------------- #
QWEN_AUDIO_MODELS: List[Dict[str, Any]] = [
    {"id": "qwen3-asr-flash", "kind": "asr", "note": "speech-to-text fast"},
    {"id": "qwen3-asr-flash-2025-09-08", "kind": "asr", "note": "ASR dated"},
    {"id": "qwen3-asr-flash-2026-02-10", "kind": "asr", "note": "ASR dated"},
    {"id": "qwen3-asr-flash-realtime", "kind": "asr", "note": "streaming ASR"},
    {"id": "qwen3-asr-flash-realtime-2025-10-27", "kind": "asr", "note": "streaming ASR dated"},
    {"id": "qwen3-asr-flash-realtime-2026-02-10", "kind": "asr", "note": "streaming ASR dated"},
    {"id": "qwen3-asr-flash-filetrans", "kind": "asr", "note": "file transcription"},
    {"id": "qwen3-asr-flash-filetrans-2025-11-17", "kind": "asr", "note": "file transcription dated"},
    {"id": "qwen-audio-3.0-asr-flash", "kind": "asr", "note": "audio 3.0 ASR"},
    {"id": "qwen-audio-3.0-asr-flash-streaming", "kind": "asr", "note": "audio 3.0 ASR streaming"},
    {"id": "qwen-audio-3.0-asr-flash-filetrans", "kind": "asr", "note": "audio 3.0 ASR file"},
    {"id": "fun-asr", "kind": "asr", "note": "FunASR general"},
    {"id": "fun-asr-2025-08-25", "kind": "asr", "note": "FunASR dated"},
    {"id": "fun-asr-2025-11-07", "kind": "asr", "note": "FunASR dated"},
    {"id": "fun-asr-flash-2026-06-15", "kind": "asr", "note": "FunASR flash"},
    {"id": "fun-asr-mtl", "kind": "asr", "note": "FunASR multilingual"},
    {"id": "fun-asr-mtl-2025-08-25", "kind": "asr", "note": "FunASR MTL dated"},
    {"id": "fun-asr-realtime", "kind": "asr", "note": "FunASR streaming"},
    {"id": "fun-asr-realtime-2025-11-07", "kind": "asr", "note": "FunASR streaming dated"},
    {"id": "qwen3-tts-flash", "kind": "tts", "note": "text-to-speech fast"},
    {"id": "qwen3-tts-flash-2025-09-18", "kind": "tts", "note": "TTS dated"},
    {"id": "qwen3-tts-flash-2025-11-27", "kind": "tts", "note": "TTS dated"},
    {"id": "qwen3-tts-flash-realtime", "kind": "tts", "note": "streaming TTS"},
    {"id": "qwen3-tts-flash-realtime-2025-09-18", "kind": "tts", "note": "streaming TTS dated"},
    {"id": "qwen3-tts-flash-realtime-2025-11-27", "kind": "tts", "note": "streaming TTS dated"},
    {"id": "qwen3-tts-instruct-flash", "kind": "tts", "note": "instructable TTS"},
    {"id": "qwen3-tts-instruct-flash-2026-01-26", "kind": "tts", "note": "instruct TTS dated"},
    {"id": "qwen3-tts-instruct-flash-realtime", "kind": "tts", "note": "instruct streaming TTS"},
    {"id": "qwen3-tts-instruct-flash-realtime-2026-01-22", "kind": "tts", "note": "instruct streaming dated"},
    {"id": "qwen3-tts-vc-2026-01-22", "kind": "tts", "note": "voice clone"},
    {"id": "qwen3-tts-vc-realtime-2025-11-27", "kind": "tts", "note": "voice clone streaming dated"},
    {"id": "qwen3-tts-vc-realtime-2026-01-15", "kind": "tts", "note": "voice clone streaming dated"},
    {"id": "qwen3-tts-vd-2026-01-26", "kind": "tts", "note": "voice design"},
    {"id": "qwen3-tts-vd-realtime-2025-12-16", "kind": "tts", "note": "voice design streaming dated"},
    {"id": "qwen3-tts-vd-realtime-2026-01-15", "kind": "tts", "note": "voice design streaming dated"},
    {"id": "qwen-audio-3.0-tts-flash", "kind": "tts", "note": "audio 3.0 TTS"},
    {"id": "qwen-audio-3.0-tts-plus", "kind": "tts", "note": "audio 3.0 TTS plus"},
    {"id": "cosyvoice-v3-flash", "kind": "tts", "note": "CosyVoice fast"},
    {"id": "cosyvoice-v3-plus", "kind": "tts", "note": "CosyVoice plus"},
    {"id": "qwen-voice-enrollment", "kind": "tts", "note": "voice enrollment"},
    {"id": "qwen-voice-design", "kind": "tts", "note": "voice design"},
    {"id": "qwen3-omni-30b-a3b-captioner", "kind": "captioner", "note": "audio/video captioning"},
    {"id": "qwen3-livetranslate-flash", "kind": "translate", "note": "live translation"},
    {"id": "qwen3-livetranslate-flash-2025-12-01", "kind": "translate", "note": "live translation dated"},
    {"id": "qwen3-livetranslate-flash-realtime", "kind": "translate", "note": "streaming translation"},
    {"id": "qwen3-livetranslate-flash-realtime-2025-09-22", "kind": "translate", "note": "streaming translation dated"},
    {"id": "qwen3.5-livetranslate-flash-realtime", "kind": "translate", "note": "3.5 streaming translation"},
    {"id": "qwen3.5-livetranslate-flash-realtime-2026-05-19", "kind": "translate", "note": "3.5 streaming dated"},
]

# --------------------------------------------------------------------------- #
# Qwen embedding / rerank models — DashScope embeddings API (the knowledge
# base already uses text-embedding-v4 when DashScope is the active provider).
# --------------------------------------------------------------------------- #
QWEN_EMBEDDING_MODELS: List[Dict[str, Any]] = [
    {"id": "text-embedding-v4", "kind": "embedding", "note": "default knowledge-base embedding (free)"},
    {"id": "text-embedding-v3", "kind": "embedding", "note": "embedding v3 (free)"},
    {"id": "tongyi-embedding-vision-flash", "kind": "embedding", "note": "multimodal embedding fast"},
    {"id": "tongyi-embedding-vision-plus", "kind": "embedding", "note": "multimodal embedding plus"},
    {"id": "qwen3-rerank", "kind": "rerank", "note": "reranking for retrieval"},
]

# --------------------------------------------------------------------------- #
# Direct third-party chat models (paid, estimated OpenRouter-equivalent $/M).
# --------------------------------------------------------------------------- #
EXTRA_CHAT_MODELS: List[Dict[str, Any]] = [
    {"id": "moonshotai/kimi-k3", "family": "kimi", "context": 262_144, "vision": False,
     "cost_in_per_million": 2.20, "cost_out_per_million": 8.00,
     "note": "Kimi flagship (direct api.moonshot.ai)"},
    {"id": "moonshotai/kimi-k2.7-code", "family": "kimi", "context": 262_144, "vision": False,
     "cost_in_per_million": 0.60, "cost_out_per_million": 2.40,
     "note": "Kimi coder (direct api.moonshot.ai)"},
    {"id": "moonshotai/kimi-k2.6", "family": "kimi", "context": 262_144, "vision": False,
     "cost_in_per_million": 0.60, "cost_out_per_million": 2.40,
     "note": "Kimi general (direct api.moonshot.ai)"},
    {"id": "minimax/minimax-m3", "family": "minimax", "context": 262_144, "vision": False,
     "cost_in_per_million": 0.80, "cost_out_per_million": 2.20,
     "note": "MiniMax M3 (direct api.minimax.io)"},
    {"id": "minimax/MiniMax-M1", "family": "minimax", "context": 262_144, "vision": False,
     "cost_in_per_million": 0.80, "cost_out_per_million": 2.20,
     "note": "MiniMax M1 (direct api.minimax.io)"},
    {"id": "minimax/MiniMax-Text-01", "family": "minimax", "context": 1_000_000, "vision": False,
     "cost_in_per_million": 0.40, "cost_out_per_million": 1.10,
     "note": "MiniMax Text-01 (direct api.minimax.io)"},
    {"id": "glm/glm-4.7", "family": "glm", "context": 262_144, "vision": False,
     "cost_in_per_million": 0.50, "cost_out_per_million": 2.00,
     "note": "GLM-4.7 (direct open.bigmodel.cn)"},
    {"id": "glm/glm-4.6", "family": "glm", "context": 262_144, "vision": False,
     "cost_in_per_million": 0.50, "cost_out_per_million": 2.00,
     "note": "GLM-4.6 (direct open.bigmodel.cn)"},
    {"id": "glm/glm-4.5", "family": "glm", "context": 262_144, "vision": False,
     "cost_in_per_million": 0.50, "cost_out_per_million": 2.00,
     "note": "GLM-4.5 (direct open.bigmodel.cn)"},
]


def grouped_catalog() -> Dict[str, List[Dict[str, Any]]]:
    """Return the full catalog grouped for the Models UI."""
    chat = [
        {**m, "id": f"dashscope/{m['id']}", "provider": "dashscope", "free_tier": True}
        for m in QWEN_CHAT_MODELS
    ]
    extra = [{**m, "provider": m["id"].split("/", 1)[0], "free_tier": False} for m in EXTRA_CHAT_MODELS]
    audio = [
        {**m, "id": f"dashscope/{m['id']}", "provider": "dashscope", "free_tier": True}
        for m in QWEN_AUDIO_MODELS
    ]
    emb = [
        {**m, "id": f"dashscope/{m['id']}", "provider": "dashscope", "free_tier": True}
        for m in QWEN_EMBEDDING_MODELS
    ]
    return {
        "chat": chat + extra,
        "audio": audio,
        "embedding": emb,
    }


__all__ = [
    "QWEN_CHAT_MODELS",
    "QWEN_AUDIO_MODELS",
    "QWEN_EMBEDDING_MODELS",
    "EXTRA_CHAT_MODELS",
    "grouped_catalog",
]
