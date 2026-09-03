import json
import subprocess
from unittest.mock import patch

from guduck.speaker import _run
from guduck_poc.speakers.direct import DirectSpeaker


def test_direct_speaker_uses_l05c_play_text_action_without_a_shell() -> None:
    captured = {}

    def run(command: list[str], env: dict[str, str], timeout: int):
        captured.update(command=command, env=env, timeout=timeout)
        return 0, '{"code":0}', ""

    speaker = DirectSpeaker("user", "pass", "did", run=run)
    result = speaker.speak("zebra. zebra.")
    payload = json.loads(captured["command"][4])
    assert captured["command"][:3] == ["python", "-m", "miservice"]
    assert payload == {"did": "did", "siid": 5, "aiid": 3, "in": ["zebra. zebra."]}
    assert result.success is True


def test_direct_speaker_records_timeout_without_leaking_credentials() -> None:
    def run(command: list[str], env: dict[str, str], timeout: int):
        raise subprocess.TimeoutExpired(command, timeout, stderr="user password did")

    speaker = DirectSpeaker("user", "password", "did", run=run)

    result = speaker.speak("hello")

    assert result.success is False
    assert result.code == "timeout"
    assert all(secret not in result.error for secret in ("user", "password", "did"))


def test_direct_speaker_rejects_invalid_response_and_never_persists_stderr() -> None:
    def run(command: list[str], env: dict[str, str], timeout: int):
        return 0, "not-json", "https://example.test/?token=secret; Cookie: secret"

    speaker = DirectSpeaker("user", "password", "did", run=run)

    result = speaker.speak("hello")

    assert result.success is False
    assert result.code == "invalid_response"
    assert result.error == "subprocess_error"


def test_direct_speaker_does_not_forward_unrelated_environment_secrets() -> None:
    captured = {}

    def run(command: list[str], env: dict[str, str], timeout: int):
        captured.update(env)
        return 0, '{"code":0}', ""

    with patch.dict("os.environ", {"HA_ACCESS_TOKEN": "ha-secret", "PATH": "/bin"}, clear=True):
        DirectSpeaker("user", "password", "did", run=run).speak("hello")

    assert "HA_ACCESS_TOKEN" not in captured
    assert captured["PATH"] == "/bin"


def test_direct_speaker_passes_persistent_home_to_miservice() -> None:
    captured = {}

    def run(command: list[str], env: dict[str, str], timeout: int):
        captured.update(env)
        return 0, '{"code":0}', ""

    with patch.dict(
        "os.environ", {"HOME": "/var/lib/guduck/mi", "PATH": "/bin"}, clear=True
    ):
        DirectSpeaker("user", "password", "did", run=run).speak("hello")

    assert captured["HOME"] == "/var/lib/guduck/mi"


def test_direct_speaker_classifies_login_failure_without_storing_raw_error() -> None:
    def run(command: list[str], env: dict[str, str], timeout: int):
        return 1, "", "Exception on login owner@example.test: Login auth failed"

    result = DirectSpeaker("owner@example.test", "password", "did", run=run).speak("hello")

    assert result.success is False
    assert result.code == "reauth_required"
    assert result.error == "reauth_required"
    assert "owner@example.test" not in result.error


def test_direct_speaker_classifies_login_response_code_as_reauthentication() -> None:
    def run(command: list[str], env: dict[str, str], timeout: int):
        return 0, '{"code":70016}', ""

    result = DirectSpeaker("user", "password", "did", run=run).speak("hello")

    assert result.code == "reauth_required"
    assert result.error == "reauth_required"


def test_miservice_runner_never_reads_interactive_input() -> None:
    captured = {}

    def fake_run(command: list[str], **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    with patch("guduck.speaker.subprocess.run", fake_run):
        _run(["python", "-m", "miservice"], {}, 30)

    assert captured["stdin"] is subprocess.DEVNULL
