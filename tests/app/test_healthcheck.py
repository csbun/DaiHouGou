from guduck.healthcheck import health_url


def test_healthcheck_uses_configured_lan_bind_address() -> None:
    assert health_url({"WEB_HOST": "192.168.10.20", "WEB_PORT": "9080"}) == (
        "http://192.168.10.20:9080/healthz"
    )


def test_healthcheck_maps_wildcard_bind_to_loopback() -> None:
    assert health_url({"WEB_HOST": "0.0.0.0", "WEB_PORT": "8080"}) == (
        "http://127.0.0.1:8080/healthz"
    )
