from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import GlanceDeckApiClient, GlanceDeckApiError
from .const import COORDINATOR_UPDATE_SECONDS, DOMAIN


class GlanceDeckCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Fetch device state from the control plane."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, api: GlanceDeckApiClient) -> None:
        super().__init__(
            hass,
            logger=logging.getLogger(__name__),
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=timedelta(seconds=COORDINATOR_UPDATE_SECONDS),
        )
        self.api = api

    async def _async_device_details(self, device_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        display, pages = await asyncio.gather(
            self.api.async_get_display(device_id),
            self.api.async_get_device_pages(device_id),
        )
        return display, pages

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        try:
            devices = await self.api.async_get_devices()
            get_alerts = getattr(self.api, "async_get_alerts", None)
            alerts = await get_alerts() if get_alerts is not None else []
            active_alerts: dict[str, list[dict[str, Any]]] = {}
            for alert in alerts:
                for device_id in alert.get("device_ids", []):
                    if isinstance(device_id, str):
                        active_alerts.setdefault(device_id, []).append(alert)
            # Fetch every device's display and page configuration concurrently. Awaiting them one
            # device at a time makes each refresh 2N sequential round trips, so a modest number of
            # devices pushes the poll past its own interval.
            tracked = [device for device in devices if isinstance(device.get("id"), str)]
            details = await asyncio.gather(
                *(
                    self._async_device_details(str(device["id"]))
                    for device in tracked
                ),
                return_exceptions=True,
            )
            result: dict[str, dict[str, Any]] = {}
            for device, detail in zip(tracked, details, strict=True):
                device_id = str(device["id"])
                if isinstance(detail, BaseException):
                    # One unreachable device must not drop every other device from the update.
                    if isinstance(detail, GlanceDeckApiError):
                        self.logger.warning("Glance Deck device %s update failed: %s", device_id, detail)
                    else:
                        raise detail
                    continue
                display, pages = detail
                result[device_id] = {**device, "display": display, "page_configuration": pages}
                if active_alerts.get(device_id):
                    result[device_id]["active_alerts"] = active_alerts[device_id]
            return result
        except GlanceDeckApiError as error:
            raise UpdateFailed(str(error)) from error

    def device(self, device_id: str) -> dict[str, Any] | None:
        return self.data.get(device_id) if self.data else None
