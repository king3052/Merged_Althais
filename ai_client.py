"""
ai_client.py: which AI service Althais's coding, claim checks and assistant use, behind the one interface the app already
calls (client.chat.completions.create(...).choices[0].message.content), so switching services is a setting, not a rewrite.

  AI_PROVIDER=groq     Groq (the default today). No BAA: synthetic data only.
  AI_PROVIDER=vertex   Google Vertex AI (Gemini), covered by Google Cloud's BAA once it's signed for the project.
                       GOOGLE_CLOUD_PROJECT, VERTEX_LOCATION (default us-central1), VERTEX_MODEL (the stronger model, for
                       coding) and VERTEX_FAST_MODEL (for quick checks). On Cloud Run the service account's credentials are
                       used automatically; nothing to paste.

Claude (patient intake, staff document reading, training) can also run through Vertex: CLAUDE_ON_VERTEX=1 makes
anthropic_client() return Anthropic's Vertex client for the same project and region. Model names on Vertex can differ
from Anthropic's own (set INTAKE_MODEL etc. to the Vertex name).
"""
import os
import re
from types import SimpleNamespace

PROVIDER = (os.environ.get("AI_PROVIDER") or "groq").strip().lower()
PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCP_PROJECT") or ""
LOCATION = os.environ.get("VERTEX_LOCATION") or "us-central1"
VERTEX_MODEL = os.environ.get("VERTEX_MODEL") or "gemini-2.5-pro"
VERTEX_FAST_MODEL = os.environ.get("VERTEX_FAST_MODEL") or "gemini-2.5-flash"


def vertex_model_for(model: str) -> str:
    """The Gemini model standing in for a Groq model name the app asks for: the fast one for the small model, else the strong one."""
    m = (model or "").lower()
    if m.startswith("gemini"):
        return model
    small = re.search(r"(?<![0-9])(20b|8b)\b", m) or "fast" in m or "instant" in m      # gpt-oss-20b, not gpt-oss-120b
    return VERTEX_FAST_MODEL if small else VERTEX_MODEL


def _reply(text: str):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text or ""), finish_reason="stop")])


class _Completions:
    def __init__(self, owner):
        self.owner = owner

    def create(self, model=None, messages=None, temperature=None, response_format=None, max_completion_tokens=None,
               max_tokens=None, reasoning_effort=None, **_):
        return self.owner._create(model, messages or [], temperature, response_format, max_completion_tokens or max_tokens, reasoning_effort)


class VertexClient:
    """Gemini on Vertex AI, answering the OpenAI/Groq-style calls the app makes."""

    def __init__(self, timeout=None, max_retries=0, genai_client=None):
        self.timeout, self.max_retries = timeout, max_retries
        self._genai = genai_client
        self.chat = SimpleNamespace(completions=_Completions(self))

    def with_options(self, timeout=None, max_retries=None, **_):
        return VertexClient(timeout=timeout if timeout is not None else self.timeout,
                            max_retries=self.max_retries if max_retries is None else max_retries, genai_client=self._genai)

    def _client(self):
        if self._genai is None:
            from google import genai
            self._genai = genai.Client(vertexai=True, project=PROJECT or None, location=LOCATION)
        return self._genai

    def _create(self, model, messages, temperature, response_format, max_tokens, reasoning_effort):
        from google.genai import types
        system = "\n\n".join(str(m.get("content") or "") for m in messages if m.get("role") == "system")
        contents = [types.Content(role="model" if m.get("role") == "assistant" else "user", parts=[types.Part(text=str(m.get("content") or ""))])
                    for m in messages if m.get("role") != "system"]
        name = vertex_model_for(model)
        cfg = {"temperature": temperature}
        if system:
            cfg["system_instruction"] = system
        if max_tokens:
            cfg["max_output_tokens"] = int(max_tokens) + (1024 if "pro" in name else 0)   # room for the model's thinking on top of the answer
        if reasoning_effort == "low" and "flash" in name:
            cfg["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
        rf = response_format or {}
        if rf.get("type") in ("json_object", "json_schema"):
            cfg["response_mime_type"] = "application/json"
            schema = (rf.get("json_schema") or {}).get("schema")
            if schema:
                cfg["response_json_schema"] = schema
        if self.timeout:
            cfg["http_options"] = types.HttpOptions(timeout=int(float(self.timeout) * 1000))
        cfg = {k: v for k, v in cfg.items() if v is not None}
        try:
            r = self._client().models.generate_content(model=name, contents=contents, config=types.GenerateContentConfig(**cfg))
        except Exception as e:
            if "response_json_schema" in cfg and "schema" in str(e).lower() and "429" not in str(e):
                cfg.pop("response_json_schema")       # a schema Gemini won't take: ask for plain JSON; callers read JSON out of the text
                r = self._client().models.generate_content(model=name, contents=contents, config=types.GenerateContentConfig(**cfg))
            else:
                raise
        return _reply(getattr(r, "text", "") or "")


def make_client(timeout=25, max_retries=0):
    """The app's AI client for the configured provider."""
    if PROVIDER == "vertex":
        return VertexClient(timeout=timeout, max_retries=max_retries)
    from groq import Groq
    return Groq(api_key=os.environ.get("GROQ_API_KEY"), timeout=timeout, max_retries=max_retries)


def anthropic_client(timeout=60.0, max_retries=1):
    """Claude: Anthropic's API, or Claude on Vertex AI (same Google Cloud BAA) with CLAUDE_ON_VERTEX=1."""
    import anthropic
    if os.environ.get("CLAUDE_ON_VERTEX") == "1":
        return anthropic.AnthropicVertex(project_id=PROJECT or None, region=os.environ.get("CLAUDE_VERTEX_REGION") or LOCATION,
                                         timeout=timeout, max_retries=max_retries)
    return anthropic.Anthropic(timeout=timeout, max_retries=max_retries)


def claude_ready() -> bool:
    """Whether Claude can be called: an Anthropic key, or Claude on Vertex switched on."""
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("CLAUDE_ON_VERTEX") == "1")


def status() -> dict:
    """What the admin console shows: which service handles AI, and whether it's one covered by a BAA."""
    return {"provider": PROVIDER, "project": PROJECT if PROVIDER == "vertex" else "", "location": LOCATION if PROVIDER == "vertex" else "",
            "model": VERTEX_MODEL if PROVIDER == "vertex" else "", "fast_model": VERTEX_FAST_MODEL if PROVIDER == "vertex" else "",
            "claude_on_vertex": os.environ.get("CLAUDE_ON_VERTEX") == "1"}
