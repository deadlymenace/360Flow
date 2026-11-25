"""Atlas credit checks, hard floor enforcement, and admin /health status."""

from __future__ import annotations

import asyncio

import pytest

from services.world_gen import (
    WORLD_CREDITS_MESSAGE,
    ProviderFailure,
    _require_credits,
    clear_world_credits_cache,
    credits_required,
    world_credits_balance,
)
import services.world_gen as world_gen


def setup_function():
    clear_world_credits_cache()


def teardown_function():
    clear_world_credits_cache()


def test_atlas_credit_costs_match_rate_card():
    assert credits_required("atlas-draft") == 520
    assert credits_required("atlas-refined") == 5020


def test_atlas_credits_balance_returns_none_without_marble_endpoint():
    assert asyncio.run(world_credits_balance()) is None


def test_require_credits_enforces_hard_floor_when_balance_known(monkeypatch):
    async def low_balance(api_key=None) -> float:
        return 200.0

    monkeypatch.setattr(world_gen, "world_credits_balance", low_balance)
    with pytest.raises(ProviderFailure) as caught:
        asyncio.run(_require_credits("atlas-draft"))
    assert caught.value.client_message == WORLD_CREDITS_MESSAGE
    assert caught.value.status_code == 402


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
