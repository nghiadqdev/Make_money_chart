from core.state_machine import ControlPlane, StateMachine
from core.types import BotState


def test_state_machine_blocks_entries_while_stopping():
    sm = StateMachine()
    assert sm.allow_new_entries
    sm.request_stop("test")
    assert sm.state is BotState.STOPPING
    assert not sm.allow_new_entries
    sm.request_stop("again")  # idempotent
    assert sm.state is BotState.STOPPING
    sm.mark_stopped()
    assert sm.state is BotState.STOPPED


def test_control_plane_roundtrip(tmp_path):
    control = ControlPlane(tmp_path / "control.json")
    assert control.read() == "start"
    control.request_stop()
    assert control.read() == "stop"
    control.request_force_close()
    assert control.read() == "force_close"
    control.request_start()
    assert control.read() == "start"
    control.clear()
    assert control.read() == "start"
