"""Tests may use local IPC/fake transports, never an external network source."""

import socket

import pytest


@pytest.fixture(autouse=True)
def no_external_network(monkeypatch):
    original_connect = socket.socket.connect
    original_dns = socket.getaddrinfo
    local = {"localhost", "127.0.0.1", "::1"}

    def connect(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6) and str(address[0]) not in local:
            raise AssertionError("External network access is forbidden during tests")
        return original_connect(sock, address)

    def resolve(host, *args, **kwargs):
        if host is not None and str(host) not in local:
            raise AssertionError("External DNS is forbidden during tests")
        return original_dns(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket, "getaddrinfo", resolve)
