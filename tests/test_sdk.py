import prism_sdk


def test_sdk_version() -> None:
    assert prism_sdk.__version__ == "0.1.0"


def test_prism_client_noop_calls_litellm_and_returns_response(monkeypatch) -> None:
    captured = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        return {
            "choices": [{"message": {"content": "pong"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    monkeypatch.setattr(prism_sdk.litellm, "completion", fake_completion)
    client = prism_sdk.PrismClient(sink="noop")

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": "ping"}],
        conversation_id="01935b3f-0000-7000-8000-000000000002",
        message_id="01935b3f-0000-7000-8000-000000000003",
    )

    assert response["choices"][0]["message"]["content"] == "pong"
    assert captured["model"] == "gpt-4o"
