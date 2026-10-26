import httpx

from gatekeeper.proxy.upstream import filter_request_headers, filter_response_headers


def test_hop_by_hop_headers_are_stripped_from_requests():
    headers = {
        "Connection": "keep-alive",
        "Content-Length": "123",
        "X-Custom": "keep-me",
        "Host": "client-facing-host",
    }
    filtered = filter_request_headers(headers)
    assert "Connection" not in filtered
    assert "Content-Length" not in filtered
    assert filtered["X-Custom"] == "keep-me"


def test_client_credentials_are_never_forwarded_upstream():
    headers = {"Authorization": "Bearer secret", "X-API-Key": "gk_abc_def", "X-Custom": "keep-me"}
    filtered = filter_request_headers(headers)
    assert "Authorization" not in filtered
    assert "X-API-Key" not in filtered
    assert filtered["X-Custom"] == "keep-me"


def test_response_headers_strip_hop_by_hop_and_server_info():
    headers = httpx.Headers(
        {
            "Connection": "close",
            "Transfer-Encoding": "chunked",
            "Server": "some-internal-server/1.2.3",
            "Content-Type": "application/json",
        }
    )
    filtered = dict(filter_response_headers(headers))
    assert "connection" not in filtered
    assert "transfer-encoding" not in filtered
    assert "server" not in filtered
    assert filtered["content-type"] == "application/json"
