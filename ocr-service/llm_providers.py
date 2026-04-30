import base64
import logging
import os
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

_CAPTION_PROMPT = """Analyze this image extracted from a document.

CRITICAL: Output ONLY the three sections below. No preamble, no steps, no extra text.

[ASCII]
Reproduce the visual content as ASCII art:
- Node-edge graphs: NodeA ---[label]--> NodeB
- Trees: indented structure with | and -- connectors
- Tables/matrices: aligned columns with spacing
- Flowcharts: --> for flow, <condition?> for decisions
- Equations: preserve notation exactly as written
- Decorative image or photo: write NONE

[DESCRIPTION]
2-4 sentences on what the diagram represents conceptually.
Focus on relationships, flow direction, key takeaways.
Do NOT repeat the ASCII.

[CONFIDENCE]
HIGH"""


class CaptionProvider(ABC):
    """Strategy interface — all providers must implement this."""

    @abstractmethod
    def caption(self, image_bytes: bytes, ext: str) -> str:
        """Return a text caption for the given image bytes."""


class GeminiProvider(CaptionProvider):
    def __init__(self, api_key: str) -> None:
        from google import genai
        self._client = genai.Client(api_key=api_key)

    def caption(self, image_bytes: bytes, ext: str) -> str:
        from google.genai import types
        response = self._client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[
                types.Part.from_bytes(data=image_bytes, mime_type=f"image/{ext}"),
                _CAPTION_PROMPT,
            ],
        )
        return response.text


class GroqProvider(CaptionProvider):
    def __init__(self, api_key: str) -> None:
        from langchain_groq import ChatGroq
        self._llm = ChatGroq(model="meta-llama/llama-4-scout-17b-16e-instruct", api_key=api_key)

    def caption(self, image_bytes: bytes, ext: str) -> str:
        from langchain_core.messages import HumanMessage
        b64 = base64.b64encode(image_bytes).decode()
        msg = HumanMessage(content=[
            {"type": "image_url", "image_url": {"url": f"data:image/{ext};base64,{b64}"}},
            {"type": "text", "text": _CAPTION_PROMPT},
        ])
        return self._llm.invoke([msg]).content


# Singleton cache keyed by (provider_name, api_key) — per-key instances
_instances: dict[tuple[str, str], CaptionProvider] = {}

_REGISTRY: dict[str, type[CaptionProvider]] = {
    "gemini": GeminiProvider,
    "groq": GroqProvider,
}

_ENV_KEYS = {
    "gemini": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
}


def get_provider(name: str | None = None, api_key: str | None = None) -> CaptionProvider | None:
    """
    Resolve a CaptionProvider by name.
    api_key: per-request key (from request header). Falls back to env var if not provided.
    name: falls back to LLM_PROVIDER env var, then 'gemini'.
    Returns None if no key is available or provider is unknown.
    """
    resolved = (name or os.environ.get("LLM_PROVIDER", "gemini")).lower()
    cls = _REGISTRY.get(resolved)
    if cls is None:
        logger.error("Unknown LLM provider: %s. Available: %s", resolved, list(_REGISTRY))
        return None

    key = api_key or os.environ.get(_ENV_KEYS.get(resolved, ""), "")
    if not key:
        logger.warning("No API key for provider '%s'; skipping image captioning", resolved)
        return None

    cache_key = (resolved, key)
    if cache_key in _instances:
        return _instances[cache_key]
    try:
        instance = cls(api_key=key)
        _instances[cache_key] = instance
        return instance
    except Exception as e:
        logger.warning("Provider '%s' failed to initialise: %s", resolved, e)
        return None
