"""Read-only THE FINALS event detection.

Safety boundary:
- never opens the Discovery.exe process
- never reads game memory
- never injects/hooks code
- never modifies game files
- never simulates input

The parser only consumes text already written by the game's Unreal logging layer.
Shipping builds can change log wording between updates, so the rules below are
intentionally conservative and stateful rather than trying to infer combat data.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable, Optional


@dataclass(frozen=True)
class FinalsEvent:
    kind: str
    value: str = ""


class FinalsEventParser:
    """Turn newly appended Discovery log lines into jukebox context events."""

    _MENU_MARKERS = (
        "mainmenu",
        "main_menu",
        "front end",
        "frontend",
        "front_end",
        "menuworld",
    )
    _QUEUE_MARKERS = (
        "matchmaking",
        "searching for match",
        "searching for server",
        "queue started",
        "start matchmaking",
    )
    _CONNECT_MARKERS = (
        "joining server",
        "join server",
        "connecting to server",
        "connected to server",
        "welcomed by server",
        "client travel",
        "server travel",
        "pendingnetgame",
    )
    _LIVE_MARKERS = (
        "match started",
        "round started",
        "start of round",
        "gameplay started",
        "beginplay",
        "begin play",
    )
    _POST_MARKERS = (
        "postmatch",
        "post_match",
        "post match",
        "endofmatch",
        "end_of_match",
        "end of match",
        "endofround",
        "end_of_round",
        "end of round",
        "round ended",
        "match ended",
        "round over",
        "match results",
    )

    def __init__(self) -> None:
        self.state = "offline"
        self.map_name = ""
        self._waiting_for_world = False

    def _state_event(self, new_state: str) -> Optional[FinalsEvent]:
        if new_state == self.state:
            return None
        self.state = new_state
        return FinalsEvent("state", new_state)

    @staticmethod
    def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
        return any(marker in text for marker in markers)

    @staticmethod
    def _friendly_asset_name(value: str) -> str:
        value = value.split("?", 1)[0].rstrip("/\\")
        value = value.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        value = re.sub(r"^(UEDPIE_\d+_|L_|MAP_|Map_)", "", value, flags=re.IGNORECASE)
        value = re.sub(r"_(C|P|Persistent|PersistentLevel)$", "", value, flags=re.IGNORECASE)
        return value.replace("_", " ").strip()

    def _extract_map(self, line: str) -> str:
        patterns = (
            r"LoadMap:\s*([^\s,]+)",
            r"Browse:\s*([^\s,]+)",
            r"Bringing World\s+([^\s]+)",
            r"World(?:Name)?[=:]\s*([^\s,]+)",
        )
        for pattern in patterns:
            match = re.search(pattern, line, re.IGNORECASE)
            if not match:
                continue
            raw = match.group(1).strip("'\"[]()")
            # Ignore network endpoints; only surface asset-like names.
            if re.match(r"^(?:\d{1,3}\.){3}\d{1,3}", raw) or raw.startswith("127.0.0.1"):
                continue
            name = self._friendly_asset_name(raw)
            if name and name.lower() not in {"none", "null"}:
                return name
        return ""

    def feed(self, line: str) -> list[FinalsEvent]:
        events: list[FinalsEvent] = []
        low = line.lower()

        if "discovery.exe" in low or "d-discovery.exe" in low or "e-discovery.exe" in low:
            events.append(FinalsEvent("game_seen", "THE FINALS"))

        map_name = self._extract_map(line)
        if map_name and map_name != self.map_name:
            self.map_name = map_name
            events.append(FinalsEvent("map", map_name))

        # Strong terminal markers win before generic travel/load markers.
        if self._contains_any(low, self._POST_MARKERS):
            state = self._state_event("post_match")
            if state:
                events.append(state)
            self._waiting_for_world = False
            return events

        # Front-end markers are allowed to move us back to menu from any state.
        if self._contains_any(low, self._MENU_MARKERS):
            state = self._state_event("menu")
            if state:
                events.append(state)
            self._waiting_for_world = False
            return events

        if self._contains_any(low, self._QUEUE_MARKERS):
            state = self._state_event("matchmaking")
            if state:
                events.append(state)

        if self._contains_any(low, self._CONNECT_MARKERS):
            state = self._state_event("pre_match")
            if state:
                events.append(state)
            self._waiting_for_world = True

        # Unreal commonly logs a Browse/LoadMap during server travel.  Only
        # treat it as pre-match if we were already queueing; otherwise map loads
        # also occur while entering the front end and would cause false starts.
        if self.state == "matchmaking" and ("loadmap:" in low or "browse:" in low):
            state = self._state_event("pre_match")
            if state:
                events.append(state)
            self._waiting_for_world = True

        live_marker = self._contains_any(low, self._LIVE_MARKERS)
        world_ready = (
            "bringing world" in low
            or "load map complete" in low
            or "loadmap completed" in low
            or "world initialized" in low
        )
        if live_marker or (self._waiting_for_world and world_ready):
            state = self._state_event("in_match")
            if state:
                events.append(state)
            self._waiting_for_world = False

        return events

    def feed_many(self, lines: Iterable[str]) -> list[FinalsEvent]:
        result: list[FinalsEvent] = []
        for line in lines:
            result.extend(self.feed(line))
        return result
