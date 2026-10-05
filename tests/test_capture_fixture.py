"""Critère d'acceptation de la phase 1, sur la capture réelle locale (non commitée)."""
import pytest

from dofustool.tools.explore import DEFAULT_CAPTURE, run

pytestmark = pytest.mark.skipif(not DEFAULT_CAPTURE.exists(), reason="tests/fixtures/capture.pcapng absent")


def test_game_flow_is_fully_deframed():
    session, stats, _ = run(DEFAULT_CAPTURE, archive=None)
    assert session.game_states
    for state in session.game_states:
        delivered = sum(s.delivered for s in state.connection.streams.values())
        consumed = sum(d.consumed for d in state.deframers.values())
        assert consumed == delivered > 0
        assert state.leftover == 0
        assert state.stalled_bytes == 0
        assert state.undecoded_frames == 0
        assert state.framing_error is None
    assert sum(s.count for s in stats.values()) == sum(s.messages for s in session.game_states)
