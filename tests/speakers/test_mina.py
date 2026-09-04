import asyncio

from guduck.speaker import MiNASpeaker


class FakeMiNA:
    def __init__(
        self,
        *,
        result: bool = True,
        error: Exception | None = None,
        block: bool = False,
    ) -> None:
        self.result = result
        self.error = error
        self.block = block
        self.calls: list[tuple[str, str]] = []

    async def text_to_speech(self, device_id: str, text: str) -> bool:
        self.calls.append((device_id, text))
        if self.block:
            await asyncio.Event().wait()
        if self.error is not None:
            raise self.error
        return self.result


def test_mina_speaker_calls_text_to_speech_on_the_owner_loop() -> None:
    async def scenario() -> None:
        service = FakeMiNA()
        speaker = MiNASpeaker("mina-device", lambda: service, asyncio.get_running_loop())

        result = await asyncio.to_thread(speaker.speak, "zebra. zebra.")

        assert service.calls == [("mina-device", "zebra. zebra.")]
        assert result.success is True
        assert result.code == 0

    asyncio.run(scenario())


def test_mina_speaker_maps_false_response_to_safe_failure() -> None:
    async def scenario() -> None:
        speaker = MiNASpeaker(
            "mina-device",
            lambda: FakeMiNA(result=False),
            asyncio.get_running_loop(),
        )

        result = await asyncio.to_thread(speaker.speak, "hello")

        assert result.success is False
        assert result.code == "speaker_error"
        assert result.error == "speaker_error"

    asyncio.run(scenario())


def test_mina_speaker_maps_timeout_without_exposing_device_data() -> None:
    async def scenario() -> None:
        speaker = MiNASpeaker(
            "secret-device-id",
            lambda: FakeMiNA(block=True),
            asyncio.get_running_loop(),
            timeout_seconds=0.01,
        )

        result = await asyncio.to_thread(speaker.speak, "hello")

        assert result.success is False
        assert result.code == "timeout"
        assert result.error == "timeout"
        assert "secret-device-id" not in result.error

    asyncio.run(scenario())


def test_mina_speaker_classifies_auth_failure_without_raw_exception_text() -> None:
    async def scenario() -> None:
        service = FakeMiNA(
            error=RuntimeError(
                "Login auth failed for owner@example.test token=secret-token"
            )
        )
        speaker = MiNASpeaker(
            "secret-device-id",
            lambda: service,
            asyncio.get_running_loop(),
        )

        result = await asyncio.to_thread(speaker.speak, "hello")

        assert result.success is False
        assert result.code == "reauth_required"
        assert result.error == "reauth_required"
        for secret in ("owner@example.test", "secret-token", "secret-device-id"):
            assert secret not in result.error

    asyncio.run(scenario())


def test_mina_speaker_maps_non_auth_exception_to_safe_failure() -> None:
    async def scenario() -> None:
        service = FakeMiNA(error=OSError("network secret"))
        speaker = MiNASpeaker("mina-device", lambda: service, asyncio.get_running_loop())

        result = await asyncio.to_thread(speaker.speak, "hello")

        assert result.success is False
        assert result.code == "speaker_error"
        assert result.error == "speaker_error"
        assert "network secret" not in result.error

    asyncio.run(scenario())


def test_mina_speaker_maps_closed_owner_loop_to_safe_failure() -> None:
    service = FakeMiNA()
    loop = asyncio.new_event_loop()
    loop.close()
    speaker = MiNASpeaker("mina-device", lambda: service, loop)

    result = speaker.speak("hello")

    assert result.success is False
    assert result.code == "speaker_error"
    assert result.error == "speaker_error"
    assert service.calls == []


def test_mina_speaker_gate_prevents_call_after_auth_failure() -> None:
    async def scenario() -> None:
        blocked = True
        service = FakeMiNA()
        speaker = MiNASpeaker(
            "mina-device",
            lambda: service,
            asyncio.get_running_loop(),
            is_authorized=lambda: not blocked,
        )

        result = await asyncio.to_thread(speaker.speak, "hello")

        assert result.code == "reauth_required"
        assert service.calls == []

    asyncio.run(scenario())
