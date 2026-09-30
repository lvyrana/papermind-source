"""Bounded public-HTTPS PDF fetches. DNS policy is enforced at TCP connect time."""
from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
import ssl
import tempfile
import threading
from urllib.parse import urljoin

import httpcore
import httpx
from fastapi import HTTPException
from fastapi.responses import FileResponse


class UnsafeTarget(ValueError):
    pass


class TemporaryPDFResponse(FileResponse):
    """Delete the spool even if the receiving client disconnects mid-response."""
    async def __call__(self, scope, receive, send):
        try:
            await asyncio.wait_for(super().__call__(scope, receive, send), timeout=60)
        finally:
            try:
                os.unlink(self.path)
            except FileNotFoundError:
                pass
            if getattr(self, "release_slot", None):
                self.release_slot()
                self.release_slot = None


def public_ip(value):
    if "%" in value:
        return False
    address = ipaddress.ip_address(value)
    if address.version == 6 and (address.ipv4_mapped or address.sixtofour or address.teredo):
        return False
    return address.is_global and not (address.is_multicast or address.is_reserved)


def validate_url(value):
    if len(value) > 8192 or any(ord(c) < 33 for c in value) or "\\" in value:
        raise UnsafeTarget("Invalid PDF URL")
    try:
        url = httpx.URL(value)
        if url.scheme != "https" or not url.host or url.port not in (None, 443):
            raise ValueError()
        if url.userinfo or url.fragment or "%" in url.host:
            raise ValueError()
        try:
            ipaddress.ip_address(url.host)
        except ValueError:
            if url.host.rstrip(".").lower() in ("localhost",) or "." not in url.host.rstrip("."):
                raise ValueError()
        else:
            if not public_ip(url.host):
                raise ValueError()
    except (ValueError, httpx.InvalidURL):
        raise UnsafeTarget("Only public HTTPS PDF URLs on port 443 are allowed") from None
    return str(url)


async def resolve_public(host, port):
    addresses = await asyncio.get_running_loop().getaddrinfo(
        host, port, type=socket.SOCK_STREAM,
    )
    ips = list(dict.fromkeys(item[4][0] for item in addresses))
    if not ips or not all(public_ip(ip) for ip in ips):
        raise UnsafeTarget("PDF host resolves to a non-public address")
    return ips


class PublicNetworkBackend(httpcore.AsyncNetworkBackend):
    """Connect to the checked numeric address; TLS still verifies the URL host."""
    def __init__(self):
        self.backend = httpcore.AnyIOBackend()

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        if port != 443:
            raise UnsafeTarget("PDF port is not allowed")
        ips = await asyncio.wait_for(resolve_public(host, port), timeout=timeout or 10)
        last_error = None
        for ip in ips:
            try:
                return await self.backend.connect_tcp(
                    ip, port, timeout=timeout, local_address=local_address, socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as error:
                last_error = error
        raise last_error

    async def sleep(self, seconds):
        await asyncio.sleep(seconds)


def new_pool():
    # Direct connections only: environment proxies must not resolve unchecked hosts.
    return httpcore.AsyncConnectionPool(
        ssl_context=ssl.create_default_context(), network_backend=PublicNetworkBackend(),
        max_connections=1, max_keepalive_connections=0, retries=0,
    )


_downloads = threading.BoundedSemaphore(4)


async def _download(url, max_bytes):
    target = validate_url(url)
    async with new_pool() as pool:
        for hop in range(6):
            async with pool.stream(
                "GET", target,
                headers={"User-Agent": "PaperMind/1.0", "Accept": "application/pdf", "Accept-Encoding": "identity"},
                extensions={"timeout": {"connect": 10.0, "read": 15.0, "write": 10.0, "pool": 5.0}},
            ) as response:
                headers = httpx.Headers(response.headers)
                if response.status in (301, 302, 303, 307, 308):
                    location = headers.get("location")
                    if not location or hop == 5:
                        raise HTTPException(502, "PDF redirect limit exceeded")
                    target = validate_url(urljoin(target, location))
                    continue
                if response.status != 200:
                    raise HTTPException(502, "PDF server did not return a complete file")
                if "pdf" not in headers.get("content-type", "").lower():
                    # A browser redirect from the authenticated same-origin fetch
                    # could forward its custom X-User-ID header to an external site.
                    raise HTTPException(415, "The source returned a web page; open the original link instead")
                if headers.get("content-encoding", "identity").lower() != "identity":
                    raise HTTPException(415, "Compressed PDF transfer is not supported")
                try:
                    length = int(headers.get("content-length", "0"))
                except ValueError:
                    raise HTTPException(502, "Invalid PDF content length") from None
                if length < 0 or length > max_bytes:
                    raise HTTPException(413, "PDF exceeds download limit")
                filename = None
                try:
                    with tempfile.NamedTemporaryFile(prefix="papermind-proxy-", suffix=".pdf", delete=False) as output:
                        filename = output.name
                        size = 0
                        prefix = b""
                        async for chunk in response.aiter_stream():
                            size += len(chunk)
                            if size > max_bytes:
                                raise HTTPException(413, "PDF exceeds download limit")
                            prefix = (prefix + chunk)[:1024] if len(prefix) < 1024 else prefix
                            output.write(chunk)
                    if b"%PDF-" not in prefix or (length and length != size):
                        raise HTTPException(502, "PDF content is invalid or incomplete")
                    result = TemporaryPDFResponse(
                        filename, media_type="application/pdf", filename="paper.pdf",
                        content_disposition_type="inline",
                        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
                    )
                    filename = None  # Response cleanup owns the completed temporary file.
                    return result
                finally:
                    if filename:
                        os.unlink(filename)


async def fetch_public_pdf(url, max_bytes=50 * 1024 * 1024):
    if not _downloads.acquire(blocking=False):
        raise HTTPException(503, "PDF downloads are busy; retry shortly")
    release_here = True
    try:
        response = await asyncio.wait_for(_download(url, max_bytes), timeout=60)
        if isinstance(response, TemporaryPDFResponse):
            response.release_slot = _downloads.release
            release_here = False
        return response
    except UnsafeTarget as error:
        raise HTTPException(400, str(error)) from None
    except (httpcore.NetworkError, httpcore.ProtocolError, httpcore.TimeoutException,
            OSError, asyncio.TimeoutError):
        # Do not disclose internal addresses, URL query tokens, or transport errors.
        raise HTTPException(502, "PDF download failed or timed out") from None
    finally:
        if release_here:
            _downloads.release()
