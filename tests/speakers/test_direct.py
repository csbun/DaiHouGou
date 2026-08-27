import json
import subprocess
from unittest.mock import patch

from daihougou_poc.speakers.direct import DirectSpeaker


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
