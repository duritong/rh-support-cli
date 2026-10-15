"""Socket-level loopback guard, also importable by standalone sitecustomize."""

import ipaddress
import socket
from contextlib import contextmanager
from unittest.mock import patch


def _loopback(host):
    if isinstance(host, bytes):
        host = host.decode("ascii", errors="replace")
    if str(host).lower().rstrip(".") == "localhost":
        return True
    try:
        address = ipaddress.ip_address(host)
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            return address.ipv4_mapped.is_loopback
        return address.is_loopback
    except ValueError:
        return False


@contextmanager
def network_guard():
    resolve = socket.getaddrinfo
    connect = socket.socket.connect
    connect_ex = socket.socket.connect_ex

    def guarded_resolve(host, *args, **kwargs):
        if host is not None and not _loopback(host):
            raise RuntimeError("Offline tests blocked non-loopback DNS.")
        return resolve(host, *args, **kwargs)

    def guarded_connect(sock, address):
        if sock.family != socket.AF_UNIX and not _loopback(address[0]):
            raise RuntimeError("Offline tests blocked non-loopback connection.")
        return connect(sock, address)

    def guarded_connect_ex(sock, address):
        if sock.family != socket.AF_UNIX and not _loopback(address[0]):
            raise RuntimeError("Offline tests blocked non-loopback connection.")
        return connect_ex(sock, address)

    with (
        patch.object(socket, "getaddrinfo", guarded_resolve),
        patch.object(socket.socket, "connect", guarded_connect),
        patch.object(socket.socket, "connect_ex", guarded_connect_ex),
    ):
        yield
