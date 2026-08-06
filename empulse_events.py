"""Safe, read-only EMPULSE log event detection.

This module deliberately does not open the game process, inspect memory, inject
code, or modify EMPULSE files. It only parses lines appended to Orion.log.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable, Optional


@dataclass(frozen=True)
class EmpulseEvent:
    kind: str
    value: str = ""


class EmpulseEventParser:
    """Turn Orion.log lines into stable jukebox events."""

    MENU_MAP = "/Game/Maps/MainMenu/MainMenu_Orion_V2"

    def __init__(self) -> None:
        self.state = "offline"
        self.map_name = ""
        self.mode_name = ""
        self._practice_pending = False
        self._waiting_for_live_play = False

    def _state_event(self, new_state: str) -> Optional[EmpulseEvent]:
        if new_state == self.state:
            return None
        self.state = new_state
        return EmpulseEvent("state", new_state)

    @staticmethod
    def _friendly_asset_name(value: str) -> str:
        value = value.rsplit("/", 1)[-1]
        for prefix in ("Default__EXP_", "EXP_", "MAP_"):
            if value.startswith(prefix):
                value = value[len(prefix):]
        value = re.sub(r"_(C|Main)$", "", value)
        return value.replace("_", " ").strip()

    def feed(self, line: str) -> list[EmpulseEvent]:
        events: list[EmpulseEvent] = []

        if "OrionClient-Win64-Shipping.exe" in line:
            events.append(EmpulseEvent("game_seen", "EMPULSE"))

        if "Variant=PortalWarsGameVariant:GV_PracticeRange" in line:
            self._practice_pending = True

        welcomed = re.search(r"Welcomed by server \(Level: ([^,\)]+)", line)
        if welcomed:
            self.map_name = self._friendly_asset_name(welcomed.group(1))
            events.append(EmpulseEvent("map", self.map_name))

        local_map = re.search(r"LoadMap: (/MAP_[^?\s]+)", line)
        if local_map and self._practice_pending:
            self.map_name = self._friendly_asset_name(local_map.group(1))
            events.append(EmpulseEvent("map", self.map_name))

        if "WBP_Orion_PreMatchTransitionStinger" in line or "SERVER BOOTING:" in line:
            state = self._state_event("pre_match")
            if state:
                events.append(state)
            self._waiting_for_live_play = True

        mode = re.search(
            r"Experience load complete \(CLIENT\): Default__EXP_([^\s]+?)_C", line
        )
        if mode:
            raw_mode = mode.group(1)
            if raw_mode not in {"Skip", "PostGame_Generic"}:
                self.mode_name = self._friendly_asset_name(raw_mode)
                events.append(EmpulseEvent("mode", self.mode_name))
            if raw_mode == "PostGame_Generic":
                state = self._state_event("post_match")
                if state:
                    events.append(state)
                self._waiting_for_live_play = False

        if self._practice_pending and "UEngine::LoadMap Load map complete /MAP_" in line:
            state = self._state_event("practice")
            if state:
                events.append(state)
            self._practice_pending = False
            self._waiting_for_live_play = False

        if "Phase_PreRound_LoadoutSelection_C" in line:
            self._waiting_for_live_play = True

        # The first locomotion cue after pre-round is a reliable indication that
        # control is live. It avoids starting gameplay music during map loading.
        if self._waiting_for_live_play and "GameplayCue.Locomotion" in line:
            state = self._state_event("in_match")
            if state:
                events.append(state)
            self._waiting_for_live_play = False

        # The EndOfMatch widget is also constructed while some maps initialize,
        # so it is only meaningful after we have actually entered live play.
        post_match_marker = "WBP_Orion_PostMatchTransitionStinger" in line
        end_widget_live = (
            self.state == "in_match" and "WBP_EndOfMatch_Background_C" in line
        )
        if post_match_marker or end_widget_live:
            state = self._state_event("post_match")
            if state:
                events.append(state)
            self._waiting_for_live_play = False

        streak = re.search(
            r"LogMedalTv:.*(?:event )?(?<!\d)(Double Kill|Triple Kill|Quad Kill|Penta Kill|Hexa Kill|(?:20|15|10|5) Kill Streak)",
            line,
            re.IGNORECASE,
        )
        if streak:
            normalized = {
                "double kill": "double_kill",
                "triple kill": "triple_kill",
                "quad kill": "quad_kill",
                "penta kill": "penta_kill",
                "hexa kill": "hexa_kill",
                "5 kill streak": "five_kill",
                "10 kill streak": "ten_kill",
                "15 kill streak": "fifteen_kill",
                "20 kill streak": "twenty_kill",
            }[streak.group(1).lower()]
            events.append(EmpulseEvent("stinger", normalized))

        if f"UEngine::LoadMap Load map complete {self.MENU_MAP}" in line:
            state = self._state_event("menu")
            if state:
                events.append(state)
            self._waiting_for_live_play = False
            self._practice_pending = False

        return events

    def feed_many(self, lines: Iterable[str]) -> list[EmpulseEvent]:
        result: list[EmpulseEvent] = []
        for line in lines:
            result.extend(self.feed(line))
        return result
