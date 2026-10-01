"""Config flow for Family Music."""

from __future__ import annotations

from homeassistant import config_entries

from .const import DOMAIN


class FamilyMusicConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure Family Music."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Create the single Family Music config entry."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        if user_input is None:
            return self.async_show_form(step_id="user")
        return self.async_create_entry(title="Family Music", data={})
