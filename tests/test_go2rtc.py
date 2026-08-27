from daihougou_poc.go2rtc import Go2RtcClient


def test_stream_names_are_sorted() -> None:
    responses = {
        "/api": {"host": "127.0.0.1:1984"},
        "/api/streams": {"xiaobai_25k": {}, "xiaobai": {}},
    }
    client = Go2RtcClient("http://127.0.0.1:1984", get_json=lambda path: responses[path])
    assert client.health()["host"] == "127.0.0.1:1984"
    assert client.stream_names() == ["xiaobai", "xiaobai_25k"]
