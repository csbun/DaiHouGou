from guduck_poc.speakers.home_assistant import HomeAssistantSpeaker


def test_ha_speaker_posts_configured_service_payload() -> None:
    captured = {}

    def post(url: str, token: str, payload: dict[str, object], timeout: int):
        captured.update(url=url, token=token, payload=payload, timeout=timeout)
        return 200, [{"entity_id": "notify.xiaoai"}]

    speaker = HomeAssistantSpeaker(
        base_url="http://127.0.0.1:8123",
        token="token",
        service="notify.send_message",
        entity_id="notify.xiaoai",
        text_field="message",
        extra_data={"title": "Custom title"},
        post=post,
    )
    result = speaker.speak("hello")
    assert captured["url"].endswith("/api/services/notify/send_message")
    assert captured["payload"] == {
        "entity_id": "notify.xiaoai",
        "message": "hello",
        "title": "Custom title",
    }
    assert result.success is True


def test_ha_speaker_uses_guduck_as_the_default_notification_title() -> None:
    captured = {}

    def post(url: str, token: str, payload: dict[str, object], timeout: int):
        captured.update(payload=payload)
        return 200, []

    speaker = HomeAssistantSpeaker(
        base_url="http://127.0.0.1:8123",
        token="token",
        service="notify.send_message",
        entity_id="notify.xiaoai",
        text_field="message",
        extra_data={},
        post=post,
    )

    speaker.speak("hello")

    assert captured["payload"] == {
        "entity_id": "notify.xiaoai",
        "message": "hello",
        "title": "GuDuck",
    }
