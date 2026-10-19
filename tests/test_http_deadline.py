"""Regression tests for the whole-request HTTP deadline in live_agent."""
import json

import pytest

from picklang import live_agent


class FakeSocket:
    def __init__(self):
        self.timeouts = []

    def settimeout(self, value):
        self.timeouts.append(value)


class FakeResponse:
    status = 200

    def __init__(self, chunks):
        self.chunks = iter(chunks)

    def read1(self, _size):
        return next(self.chunks)


class FakeConnection:
    def __init__(self, response):
        self.sock = FakeSocket()
        self.response = response
        self.closed = False
        self.request_args = None

    def request(self, *args, **kwargs):
        self.request_args = (args, kwargs)

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


def test_post_with_deadline_reads_response_and_closes_connection(monkeypatch):
    response = FakeResponse([b'{"ok":true}', b""])
    connection = FakeConnection(response)
    monkeypatch.setattr(live_agent.http.client, "HTTPSConnection", lambda *a, **k: connection)
    ticks = iter([10.0, 10.1, 10.2])
    monkeypatch.setattr(live_agent.time, "monotonic", lambda: next(ticks))

    result = live_agent._post_with_deadline(
        "https://example.test/api", b"{}", {"Content-Type": "application/json"}, 2
    )

    assert result == {"ok": True}
    assert connection.closed
    assert connection.sock.timeouts == pytest.approx([1.9, 1.8])


def test_post_with_deadline_stops_when_total_deadline_expires(monkeypatch):
    response = FakeResponse([b'{"partial":'])
    connection = FakeConnection(response)
    monkeypatch.setattr(live_agent.http.client, "HTTPSConnection", lambda *a, **k: connection)
    ticks = iter([10.0, 10.5, 11.1])
    monkeypatch.setattr(live_agent.time, "monotonic", lambda: next(ticks))

    with pytest.raises(TimeoutError, match="общий дедлайн"):
        live_agent._post_with_deadline(
            "https://example.test/api", b"{}", {"Content-Type": "application/json"}, 1
        )

    assert connection.closed
    assert connection.sock.timeouts == pytest.approx([0.5])


def test_post_with_deadline_rejects_non_success_http_status(monkeypatch):
    response = FakeResponse([b'{"error":"unavailable"}', b""])
    response.status = 503
    connection = FakeConnection(response)
    monkeypatch.setattr(live_agent.http.client, "HTTPSConnection", lambda *a, **k: connection)
    ticks = iter([1.0, 1.1, 1.2])
    monkeypatch.setattr(live_agent.time, "monotonic", lambda: next(ticks))

    with pytest.raises(Exception, match="HTTP 503"):
        live_agent._post_with_deadline(
            "https://example.test/api", b"{}", {"Content-Type": "application/json"}, 2
        )

    assert connection.closed
