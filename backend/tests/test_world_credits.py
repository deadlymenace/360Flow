"""World Labs credit checks: fail before a wasted generate, and show the balance to operators."""

from __future__ import annotations

import asyncio
import time

import pytest

from services.world_gen import (
    CREDIT_CACHE_TTL_S,
    WORLD_CREDITS_MESSAGE,
    ProviderFailure,
    _generate_models,
    clear_world_credits_cache,
    credits_required,
    note_out_of_credits,
    world_credits_balance,
)
import services.world_gen as world_gen


class _Response:
    def __init__(self, status_code: int, body: dict):
        self.status_code = status_code
        self._body = body
        self.text = str(body)

    def json(self):
        return self._body


class _GenerateClient:
    def __init__(self, behavior):
        self.behavior = behavior
        self.models: list[str] = []

    async def post(self, url, headers=None, json=None):
        model = json["model"]
        self.models.append(model)
        return self.behavior(model)


def setup_function():
    clear_world_credits_cache()


def teardown_function():
    clear_world_credits_cache()


def test_credit_costs_match_pano_worlds():
    assert credits_required("marble-1.0-draft") == 150
    assert credits_required("marble-1.1") == 1500
    assert credits_required("marble-1.1-plus") == 1500


def test_draft_402_does_not_start_the_full_world():
    client = _GenerateClient(
        lambda model: _Response(402, {"error": {"message": "insufficient credits"}})
    )

    with pytest.raises(ProviderFailure) as caught:
        asyncio.run(
            _generate_models(
                client,
                {},
                "room",
                "media-1",
                ["marble-1.0-draft", "marble-1.1"],
                False,
            )
        )

    assert client.models == ["marble-1.0-draft"]
    failure = caught.value
    assert failure.client_message == WORLD_CREDITS_MESSAGE
    assert failure.detail.startswith("out of credits")
    assert "HTTP 402" in failure.detail
    assert "insufficient" not in failure.client_message


def test_other_draft_failures_still_continue_to_the_full_model():
    def behavior(model: str) -> _Response:
        if "draft" in model:
            return _Response(500, {"error": {"message": "draft exploded"}})
        return _Response(
            200,
            {
                "operation_id": "op-full",
                "done": True,
                "response": {
                    "world_id": "world-full",
                    "world_marble_url": "https://marble.worldlabs.ai/world/world-full",
                },
            },
        )

    client = _GenerateClient(behavior)
    result = asyncio.run(
        _generate_models(
            client,
            {},
            "room",
            "media-1",
            ["marble-1.0-draft", "marble-1.1"],
            False,
        )
    )
    assert client.models == ["marble-1.0-draft", "marble-1.1"]
    assert result["world_id"] == "world-full"


def test_low_balance_skips_the_full_model_after_a_draft(monkeypatch):
    async def balance() -> float:
        return 200.0

    monkeypatch.setattr(world_gen, "world_credits_balance", balance)

    def behavior(model: str) -> _Response:
        return _Response(
            200,
            {
                "operation_id": f"op-{model}",
                "done": True,
                "response": {"world_id": "world-draft", "world_marble_url": "https://marble.worldlabs.ai/world/world-draft"},
            },
        )

    client = _GenerateClient(behavior)
    with pytest.raises(ProviderFailure) as caught:
        asyncio.run(
            _generate_models(
                client,
                {},
                "room",
                "media-1",
                ["marble-1.0-draft", "marble-1.1"],
                False,
            )
        )
    assert client.models == ["marble-1.0-draft"]
    assert caught.value.client_message == WORLD_CREDITS_MESSAGE
    assert caught.value.detail.startswith("out of credits")
    assert "marble-1.1 needs 1500" in caught.value.detail


def test_credits_are_cached_briefly(monkeypatch):
    monkeypatch.setenv("WORLDLABS_API_KEY", "wl-test")
    fetched = {"n": 0}

    class _CreditsClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, headers=None):
            assert url.endswith("/marble/v1/credits")
            assert headers["WLT-Api-Key"] == "wl-test"
            fetched["n"] += 1
            return _Response(200, {"remaining_credits": 1800})

    monkeypatch.setattr(world_gen.httpx, "AsyncClient", _CreditsClient)
    assert asyncio.run(world_credits_balance()) == 1800
    assert asyncio.run(world_credits_balance()) == 1800
    assert fetched["n"] == 1

    world_gen._credit_cache["at"] = time.monotonic() - CREDIT_CACHE_TTL_S - 1
    assert asyncio.run(world_credits_balance()) == 1800
    assert fetched["n"] == 2


def test_zero_balance_fails_before_generate(monkeypatch):
    monkeypatch.setenv("WORLDLABS_API_KEY", "wl-test")
    note_out_of_credits()

    def refuse(model: str) -> _Response:
        raise AssertionError(f"must not generate {model}")

    client = _GenerateClient(refuse)

    with pytest.raises(ProviderFailure) as caught:
        asyncio.run(
            _generate_models(client, {}, "room", "media-1", ["marble-1.0-draft", "marble-1.1"], False)
        )

    assert client.models == []
    assert caught.value.client_message == WORLD_CREDITS_MESSAGE
    assert caught.value.detail.startswith("out of credits (remaining 0")


def test_low_balance_is_logged_when_credits_are_fetched(monkeypatch, caplog):
    monkeypatch.setenv("WORLDLABS_API_KEY", "wl-test")
    monkeypatch.setenv("WORLDLABS_LOW_CREDIT_THRESHOLD", "6000")

    class _CreditsClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, headers=None):
            return _Response(200, {"remaining_credits": 1500})

    monkeypatch.setattr(world_gen.httpx, "AsyncClient", _CreditsClient)
    with caplog.at_level("WARNING"):
        assert asyncio.run(world_credits_balance()) == 1500
    assert any(
        record.levelname == "WARNING" and "credit balance is low" in record.message and "1500" in record.message
        for record in caplog.records
    )


def test_low_credit_threshold_defaults_to_6000(monkeypatch):
    monkeypatch.delenv("WORLDLABS_LOW_CREDIT_THRESHOLD", raising=False)
    from config import get_settings

    assert get_settings().worldlabs_low_credit_threshold == 6000


def test_admin_health_shows_the_credit_balance(client, monkeypatch):
    monkeypatch.setenv("API_KEY", "secret-key")

    async def balance():
        return 1800.0

    monkeypatch.setattr("main.world_credits_balance", balance)
    public = client.get("/health").json()
    assert public == {"status": "ok"}
    wrong = client.raw.get("/health", headers={"X-API-Key": "nope"}).json()
    assert wrong == {"status": "ok"}
    admin = client.raw.get("/health", headers={"X-API-Key": "secret-key"}).json()
    assert admin["worldlabs_credits"] == 1800
    assert admin["worldlabs_low_balance"] is True
    assert admin["storage"] == "local"
    assert admin["database"] == "sqlite"
    assert admin["staging"] == "mock"
    assert admin["world"] == "mock"
    assert admin["admin"] is True

    async def healthy():
        return 9000.0

    monkeypatch.setattr("main.world_credits_balance", healthy)
    topped_up = client.raw.get("/health", headers={"X-API-Key": "secret-key"}).json()
    assert topped_up["worldlabs_credits"] == 9000
    assert topped_up["worldlabs_low_balance"] is False
