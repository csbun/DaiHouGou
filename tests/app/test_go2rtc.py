import pytest

from guduck.go2rtc import DiscoveryError, Go2RtcClient, rtsp_stream_url


def test_stream_names_are_sorted_and_validated() -> None:
    client = Go2RtcClient(
        "http://127.0.0.1:1984/",
        get_json=lambda _path: {"xiaobai_25k": {}, "xiaobai": {}},
    )

    assert client.stream_names() == ("xiaobai", "xiaobai_25k")


@pytest.mark.parametrize("payload", [[], {"": {}}, {1: {}}])
def test_invalid_stream_payload_is_classified(payload: object) -> None:
    client = Go2RtcClient("http://127.0.0.1:1984", get_json=lambda _path: payload)

    with pytest.raises(DiscoveryError, match="^invalid_stream_response$"):
        client.stream_names()


def test_stream_id_is_url_encoded_as_one_path_segment() -> None:
    assert rtsp_stream_url("rtsp://127.0.0.1:8554/", "room/a b") == (
        "rtsp://127.0.0.1:8554/room%2Fa%20b"
    )
