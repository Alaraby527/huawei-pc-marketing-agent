"""The model deadline must bound the *whole* exchange, not one socket operation.

A gateway that trickles bytes inside the per-operation timeout resets it indefinitely,
and the previous body reader only started after the response headers had arrived. In
practice a call hung for 500+ seconds with ``LLM_TIMEOUT=120``. These tests pin the
behaviour: connect, headers and body are all capped by one wall-clock deadline.
"""
import asyncio
import socket
import threading
import time

import pytest

from app import model


def _trickling_server(chunk: bytes, interval: float):
    """A server that accepts a request and then dribbles bytes forever."""
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    state = {"sent": 0, "stop": threading.Event()}

    def serve():
        try:
            server.settimeout(30)
            connection, _ = server.accept()
            connection.settimeout(30)
            try:
                connection.recv(65536)
                while not state["stop"].wait(interval):
                    connection.send(chunk)
                    state["sent"] += 1
            except OSError:
                pass
            finally:
                connection.close()
        except OSError:
            pass
        finally:
            server.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return port, state, thread


def test_trickling_headers_cannot_beat_the_deadline():
    """Without a total deadline this call never returns (the reported hang)."""
    port, state, thread = _trickling_server(b"H", 0.4)
    started = time.monotonic()
    with pytest.raises(model.ModelError) as error:
        model._post_chat_completions(
            f"http://127.0.0.1:{port}/chat/completions",
            {"model": "x", "messages": []}, "dummy-key", timeout=2)
    elapsed = time.monotonic() - started
    state["stop"].set()
    thread.join(timeout=3)
    assert elapsed < 6, f"deadline was not enforced (took {elapsed:.1f}s)"
    assert "时限" in str(error.value) or "超时" in str(error.value)
    assert state["sent"] >= 1, "the server should have trickled at least one byte"


def test_trickling_body_cannot_beat_the_deadline():
    """Complete headers, then a body that never satisfies Content-Length."""
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    state = {"sent": 0, "stop": threading.Event()}

    def serve():
        server.settimeout(30)
        try:
            connection, _ = server.accept()
            connection.settimeout(30)
            try:
                connection.recv(65536)
                connection.send(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                                b"Content-Length: 1000000\r\n\r\n")
                while not state["stop"].wait(0.3):
                    connection.send(b" ")
                    state["sent"] += 1
            except OSError:
                pass
            finally:
                connection.close()
        except OSError:
            pass
        finally:
            server.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    started = time.monotonic()
    with pytest.raises(model.ModelError):
        model._post_chat_completions(
            f"http://127.0.0.1:{port}/chat/completions",
            {"model": "x", "messages": []}, "dummy-key", timeout=2)
    elapsed = time.monotonic() - started
    state["stop"].set()
    thread.join(timeout=3)
    assert elapsed < 6, f"deadline was not enforced (took {elapsed:.1f}s)"


def test_http_error_status_is_reported_with_its_code():
    """The JSON-compatibility fallback keys off 'HTTP 400'; keep that shape."""
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]

    def serve():
        server.settimeout(20)
        connection, _ = server.accept()
        try:
            connection.recv(65536)
            connection.send(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 2\r\n\r\n{}")
        except OSError:
            pass
        finally:
            connection.close()
            server.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    with pytest.raises(model.ModelError) as error:
        model._post_chat_completions(
            f"http://127.0.0.1:{port}/chat/completions",
            {"model": "x", "messages": []}, "dummy-key", timeout=5)
    thread.join(timeout=3)
    assert "HTTP 400" in str(error.value)


def test_model_timeout_is_clamped_to_a_safe_range(monkeypatch):
    """The deadline is still bounded by configuration even if an operator sets nonsense."""
    monkeypatch.setenv("LLM_TIMEOUT", "1")
    assert model._settings(None)["timeout"] == 10
    monkeypatch.setenv("LLM_TIMEOUT", "99999")
    assert model._settings(None)["timeout"] == 170

def _patch_connection(monkeypatch, errors):
    """Make each attempt raise the next error from `errors`."""
    state = {"n": 0}

    class Conn:
        def __init__(self, *args, **kwargs):
            pass

        def request(self, *args, **kwargs):
            index = state["n"]
            state["n"] += 1
            raise errors[min(index, len(errors) - 1)]

        def getresponse(self):
            raise AssertionError("unreachable")

        def close(self):
            pass

    monkeypatch.setattr(model.http.client, "HTTPSConnection", Conn)
    return state


def test_dns_failure_is_retried_once(monkeypatch):
    """A failure that proves nothing was sent may be retried exactly once."""
    import socket
    state = _patch_connection(monkeypatch, [socket.gaierror("dns"), ConnectionRefusedError("refused")])
    with pytest.raises(model.ModelError) as error:
        model._post_chat_completions("https://example.invalid/chat/completions",
                                     {"model": "x"}, "k", timeout=5)
    assert state["n"] == 2, "one retry, then report"
    assert "ConnectionRefusedError" in str(error.value)


def test_connection_reset_is_not_retried(monkeypatch):
    """A reset is ambiguous: the request may already have been delivered."""
    state = _patch_connection(monkeypatch, [ConnectionResetError("reset")])
    with pytest.raises(model.ModelError) as error:
        model._post_chat_completions("https://example.invalid/chat/completions",
                                     {"model": "x"}, "k", timeout=5)
    assert state["n"] == 1, "an ambiguous failure must not be repeated"
    assert "ConnectionResetError" in str(error.value)
