from pathlib import Path

from bc250_llm_mode.command_palette import palette_commands
from bc250_llm_mode.gui.routes import (
    MORE_DESTINATIONS,
    MORE_ROUTES,
    PRIMARY_ROUTES,
    Route,
)


def test_primary_and_more_navigation_inventories_are_exact_and_bounded():
    assert PRIMARY_ROUTES == (
        Route.HOME, Route.MODELS, Route.CHAT, Route.CONNECTIONS, Route.MORE)
    assert tuple(item.route for item in MORE_DESTINATIONS) == MORE_ROUTES
    assert tuple(item.route for item in MORE_DESTINATIONS) == (
        Route.ACTIVITY, Route.MAINTENANCE, Route.SYSTEM,
        Route.SETTINGS, Route.HELP)


def test_every_hidden_route_remains_in_the_command_palette():
    routes = {
        command.route for command in palette_commands(
            setup_complete=True, operational=True)
    }
    assert {
        Route.PROFILES.value, Route.ACTIVITY.value, Route.MAINTENANCE.value,
        Route.SYSTEM.value, Route.SETTINGS.value, Route.HELP.value,
        Route.REPAIR.value, Route.UPDATES.value,
    } <= routes


def test_more_page_is_one_window_and_has_no_effect_or_refresh_owner():
    source = Path("bc250_llm_mode/gui/more_page.py").read_text(encoding="utf-8")
    for forbidden in (
        "Toplevel", "messagebox", "subprocess", "systemctl", "podman",
        "request_observation(", ".after(",
    ):
        assert forbidden not in source
