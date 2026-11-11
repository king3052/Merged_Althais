"""The Vertex AI client answers the same calls the app makes to Groq, so AI_PROVIDER=vertex is a setting, not a rewrite."""
from types import SimpleNamespace

import ai_client


class FakeGenai:
    def __init__(self, reply='{"codes": []}', fail_first=None):
        self.calls, self.reply, self.fail_first = [], reply, fail_first
        self.models = SimpleNamespace(generate_content=self._gen)

    def _gen(self, model, contents, config):
        self.calls.append((model, contents, config))
        if self.fail_first and len(self.calls) == 1:
            raise RuntimeError(self.fail_first)
        return SimpleNamespace(text=self.reply)


def test_chat_call_maps_to_gemini():
    g = FakeGenai('{"codes": [{"code": "99213"}]}')
    c = ai_client.VertexClient(genai_client=g)
    schema = {"type": "object", "properties": {"codes": {"type": "array"}}, "required": ["codes"], "additionalProperties": False}
    r = c.with_options(timeout=20).chat.completions.create(
        model="openai/gpt-oss-120b", temperature=0.2, max_completion_tokens=500, reasoning_effort="low",
        messages=[{"role": "system", "content": "You are a coder."}, {"role": "user", "content": "Note text"}, {"role": "assistant", "content": "ok"}],
        response_format={"type": "json_schema", "json_schema": {"name": "x", "strict": True, "schema": schema}})
    assert r.choices[0].message.content == '{"codes": [{"code": "99213"}]}'
    model, contents, cfg = g.calls[0]
    assert model == ai_client.VERTEX_MODEL and [x.role for x in contents] == ["user", "model"]
    assert cfg.system_instruction == "You are a coder." and cfg.response_mime_type == "application/json" and cfg.response_json_schema == schema
    assert cfg.temperature == 0.2 and cfg.http_options.timeout == 20000


def test_fast_model_and_plain_json_fallback():
    g = FakeGenai('{"intent": "help"}', fail_first="400 INVALID_ARGUMENT: response schema not supported")
    r = ai_client.VertexClient(genai_client=g).chat.completions.create(
        model="openai/gpt-oss-20b", messages=[{"role": "user", "content": "q"}], temperature=0, reasoning_effort="low",
        response_format={"type": "json_schema", "json_schema": {"name": "y", "schema": {"type": "object"}}})
    assert r.choices[0].message.content == '{"intent": "help"}'
    assert g.calls[0][0] == ai_client.VERTEX_FAST_MODEL and str(g.calls[0][2].thinking_config.thinking_level).endswith("MINIMAL")
    assert g.calls[1][2].response_json_schema is None and g.calls[1][2].response_mime_type == "application/json"


def test_rate_limit_is_passed_through_for_the_app_to_handle():
    import main
    g = FakeGenai(fail_first="429 RESOURCE_EXHAUSTED. Quota exceeded")
    try:
        ai_client.VertexClient(genai_client=g).chat.completions.create(model="m", messages=[{"role": "user", "content": "q"}],
                                                                        response_format={"type": "json_schema", "json_schema": {"schema": {}}})
        assert False
    except RuntimeError as e:
        assert main._is_rate_limited(e) and len(g.calls) == 1


def test_groq_stays_the_default_and_health_answers():
    from fastapi.testclient import TestClient
    import main
    assert ai_client.PROVIDER == "groq" and ai_client.status()["provider"] == "groq"
    r = TestClient(main.app).get("/api/health")
    assert r.status_code == 200 and r.json() == {"ok": True, "ai": "groq"}


def test_claude_ready_counts_vertex(monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_ON_VERTEX"):
        monkeypatch.delenv(k, raising=False)
    assert not ai_client.claude_ready()
    monkeypatch.setenv("CLAUDE_ON_VERTEX", "1")
    assert ai_client.claude_ready()
