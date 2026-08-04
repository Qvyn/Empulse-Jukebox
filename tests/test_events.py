import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from empulse_events import EmpulseEventParser


class EventParserTests(unittest.TestCase):
    def values(self, lines):
        return [(event.kind, event.value) for event in EmpulseEventParser().feed_many(lines)]

    def test_full_context_sequence(self):
        events = self.values(
            [
                "LogGlobalStatus: UEngine::LoadMap Load map complete /Game/Maps/MainMenu/MainMenu_Orion_V2",
                "LogTemp: Warning: SERVER BOOTING: abc",
                "LogNet: Welcomed by server (Level: /MAP_Maintenance/Maintenance_Main, Game: x)",
                "LogGF1047Experience: Experience load complete (CLIENT): Default__EXP_Deathmatch_C",
                "CompleteClientAction called for phase class [Phase_PreRound_LoadoutSelection_C]",
                "Deferred gameplay cue with tag 'GameplayCue.Locomotion'",
                "WBP_EndOfMatch_Background_C",
                "LogGF1047Experience: Experience load complete (CLIENT): Default__EXP_PostGame_Generic_C",
                "LogGlobalStatus: UEngine::LoadMap Load map complete /Game/Maps/MainMenu/MainMenu_Orion_V2",
            ]
        )
        states = [value for kind, value in events if kind == "state"]
        self.assertEqual(states, ["menu", "pre_match", "in_match", "post_match", "menu"])
        self.assertIn(("map", "Maintenance"), events)
        self.assertIn(("mode", "Deathmatch"), events)

    def test_personal_streaks(self):
        events = self.values(
            [
                "LogMedalTv: Warning: Failed to emit MedalTv event Double Kill: ServersUnavailable",
                "LogMedalTv: Warning: Failed to emit MedalTv event Triple Kill: ServersUnavailable",
                "LogMedalTv: Warning: Failed to emit MedalTv event Quad Kill: ServersUnavailable",
                "LogMedalTv: Warning: Failed to emit MedalTv event 5 Kill Streak: ServersUnavailable",
            ]
        )
        self.assertEqual(
            [value for kind, value in events if kind == "stinger"],
            ["double_kill", "triple_kill", "quad_kill", "five_kill"],
        )

    def test_practice_range(self):
        events = self.values(
            [
                "Browse: /MAP_Plaza/Plaza_Main?Variant=PortalWarsGameVariant:GV_PracticeRange_Default",
                "LogLoad: LoadMap: /MAP_Plaza/Plaza_Main?Variant=x",
                "UEngine::LoadMap Load map complete /MAP_Plaza/Plaza_Main",
            ]
        )
        self.assertIn(("state", "practice"), events)


if __name__ == "__main__":
    unittest.main()

