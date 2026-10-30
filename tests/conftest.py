"""Keep every automated test offline, including tests using local audio libraries."""

import socket

import pytest


def _forbid_network(*args: object, **kwargs: object) -> None:
    """Return no result because automated tests must never connect to a network."""
    raise AssertionError("Tests must not use network access")


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Return None after disabling network connections for the duration of each test."""
    monkeypatch.setattr(socket, "create_connection", _forbid_network)
    monkeypatch.setattr(socket.socket, "connect", _forbid_network)
    monkeypatch.setattr(socket.socket, "connect_ex", _forbid_network)
