from finals_events import FinalsEventParser


def states(lines):
    parser = FinalsEventParser()
    return [event.value for line in lines for event in parser.feed(line) if event.kind == "state"]


def test_queue_to_match_to_post_match():
    assert states([
        "LogOnline: matchmaking queue started",
        "LogNet: connecting to server",
        "LogLoad: Load map complete",
        "LogGame: end of match",
    ]) == ["matchmaking", "pre_match", "in_match", "post_match"]


def test_menu_marker_returns_to_menu():
    assert states([
        "LogOnline: matchmaking",
        "LogNet: connected to server",
        "LogLoad: world initialized",
        "LogWorld: MainMenu",
    ])[-1] == "menu"


def test_map_asset_is_friendly():
    parser = FinalsEventParser()
    events = parser.feed("LogLoad: LoadMap: /Game/Maps/MAP_Test_Arena")
    maps = [event.value for event in events if event.kind == "map"]
    assert maps == ["Test Arena"]
