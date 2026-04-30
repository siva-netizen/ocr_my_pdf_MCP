import base64
import logging
import os
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

_CAPTION_PROMPT = """Analyze this image extracted from a document. Follow this exact output format:

[ASCII]
Reproduce the visual content as ASCII art using these rules:
- Node-edge graphs: use NodeA ---[label]--> NodeB format
- Trees: use indented structure with | and -- connectors
- Matrices/tables: use aligned brackets and spacing
- Flowcharts: use --> for flow, diamond <condition?> for decisions
- Equations: preserve mathematical notation exactly as written
- If image is purely decorative or photo: write NONE

[DESCRIPTION]
2-4 sentences explaining what the diagram/image represents conceptually.
Focus on relationships, flow direction, and key takeaways.
Do NOT repeat the ASCII — add semantic meaning only.

[CONFIDENCE]
Rate ASCII accuracy: HIGH / MEDIUM / LOW
HIGH = clean diagram, clear structure, unambiguous
MEDIUM = some complexity, minor uncertainty
LOW = dense/overlapping/handwritten, ASCII may be approximate"""


class CaptionProvider(ABC):
    """Strategy interface — all providers must implement this."""

    @abstractmethod
    def caption(self, image_bytes: bytes, ext: str) -> str:
        """Return a text caption for the given image bytes."""


class GeminiProvider(CaptionProvider):
    def __init__(self) -> None:
        from google import genai
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise EnvironmentError("GEMINI_API_KEY is not set")
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
    def __init__(self) -> None:
        from langchain_groq import ChatGroq
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            raise EnvironmentError("GROQ_API_KEY is not set")
        # llama-4-scout supports vision input
        self._llm = ChatGroq(model="meta-llama/llama-4-scout-17b-16e-instruct", api_key=api_key)

    def caption(self, image_bytes: bytes, ext: str) -> str:
        from langchain_core.messages import HumanMessage
        b64 = base64.b64encode(image_bytes).decode()
        msg = HumanMessage(content=[
            {"type": "image_url", "image_url": {"url": f"data:image/{ext};base64,{b64}"}},
            {"type": "text", "text": _CAPTION_PROMPT},
        ])
        return self._llm.invoke([msg]).content


# Singleton cache — one instance per provider name per process
_instances: dict[str, CaptionProvider] = {}

_REGISTRY: dict[str, type[CaptionProvider]] = {
    "gemini": GeminiProvider,
    "groq": GroqProvider,
}


def get_provider(name: str | None = None) -> CaptionProvider | None:
    """
    Resolve a CaptionProvider by name.
    Falls back to LLM_PROVIDER env var, then 'gemini'.
    Returns None if the resolved provider cannot be initialised (missing key).
    """
    resolved = (name or os.environ.get("LLM_PROVIDER", "gemini")).lower()
    if resolved in _instances:
        return _instances[resolved]
    cls = _REGISTRY.get(resolved)
    if cls is None:
        logger.error("Unknown LLM provider: %s. Available: %s", resolved, list(_REGISTRY))
        return None
    try:
        instance = cls()
        _instances[resolved] = instance
        return instance
    except EnvironmentError as e:
        logger.warning("Provider '%s' unavailable: %s", resolved, e)
        return None
