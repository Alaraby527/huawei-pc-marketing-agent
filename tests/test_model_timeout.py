import pytest

from app import model


class FakeResponse:
    def __init__(self, chunks):
        self.chunks = list(chunks)

    def read(self, _size):
        return self.chunks.pop(0) if self.chunks else b""


def test_read_with_deadline_returns_full_body():
    body = model._read_with_deadline(
        FakeResponse([b"a", b"b"]), deadline=model.time.monotonic() + 5, limit=10)
    assert body == b"ab"


def test_read_with_deadline_stops_slow_stream(monkeypatch):
    clock = iter([0, 0, 5])
    monkeypatch.setattr(model.time, "monotonic", lambda: next(clock))
    with pytest.raises(TimeoutError):
        model._read_with_deadline(FakeResponse([b"a", b"b"]), deadline=1, limit=10)


def test_read_with_deadline_rejects_oversized_body():
    with pytest.raises(model.ModelError):
        model._read_with_deadline(
            FakeResponse([b"123456"]), deadline=model.time.monotonic() + 5, limit=3)
