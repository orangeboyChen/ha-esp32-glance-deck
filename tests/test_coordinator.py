from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.glance_deck.api import GlanceDeckApiError
from custom_components.glance_deck.coordinator import GlanceDeckCoordinator


def make_coordinator(api):
    coordinator = object.__new__(GlanceDeckCoordinator)
    coordinator.api = api
    coordinator.data = None
    coordinator.logger = logging.getLogger("custom_components.glance_deck.coordinator")
    return coordinator


@pytest.mark.asyncio
async def test_coordinator_combines_device_display_and_enabled_page_configuration() -> None:
    api = SimpleNamespace(
        async_get_devices=AsyncMock(return_value=[{"id": "deck-a"}, {"id": 1}]),
        async_get_display=AsyncMock(return_value={"version": 2}),
        async_get_device_pages=AsyncMock(return_value={"enabled_page_ids": ["usage"]}),
    )
    coordinator = make_coordinator(api)
    assert await coordinator._async_update_data() == {"deck-a": {"id": "deck-a", "display": {"version": 2}, "page_configuration": {"enabled_page_ids": ["usage"]}}}
    coordinator.data = {"deck-a": {"id": "deck-a"}}
    assert coordinator.device("deck-a") == {"id": "deck-a"}
    assert coordinator.device("missing") is None


@pytest.mark.asyncio
async def test_coordinator_wraps_api_errors() -> None:
    coordinator = make_coordinator(SimpleNamespace(async_get_devices=AsyncMock(side_effect=GlanceDeckApiError("offline"))))
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


@pytest.mark.asyncio
async def test_coordinator_fetches_devices_concurrently() -> None:
    """Every device's display and pages are requested in parallel, not one device at a time."""
    in_flight = 0
    peak = 0

    async def slow_display(device_id):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0)
        in_flight -= 1
        return {"version": 2}

    api = SimpleNamespace(
        async_get_devices=AsyncMock(return_value=[{"id": "deck-a"}, {"id": "deck-b"}, {"id": "deck-c"}]),
        async_get_display=AsyncMock(side_effect=slow_display),
        async_get_device_pages=AsyncMock(return_value={"enabled_page_ids": []}),
    )
    coordinator = make_coordinator(api)
    result = await coordinator._async_update_data()
    assert set(result) == {"deck-a", "deck-b", "deck-c"}
    assert peak == 3


@pytest.mark.asyncio
async def test_coordinator_keeps_other_devices_when_one_fails() -> None:
    async def failing_display(device_id):
        if device_id == "deck-b":
            raise GlanceDeckApiError("device unreachable")
        return {"version": 2}

    api = SimpleNamespace(
        async_get_devices=AsyncMock(return_value=[{"id": "deck-a"}, {"id": "deck-b"}]),
        async_get_display=AsyncMock(side_effect=failing_display),
        async_get_device_pages=AsyncMock(return_value={"enabled_page_ids": []}),
    )
    coordinator = make_coordinator(api)
    result = await coordinator._async_update_data()
    assert set(result) == {"deck-a"}


@pytest.mark.asyncio
async def test_coordinator_propagates_unexpected_device_errors() -> None:
    api = SimpleNamespace(
        async_get_devices=AsyncMock(return_value=[{"id": "deck-a"}]),
        async_get_display=AsyncMock(side_effect=RuntimeError("boom")),
        async_get_device_pages=AsyncMock(return_value={}),
    )
    coordinator = make_coordinator(api)
    with pytest.raises(RuntimeError):
        await coordinator._async_update_data()
