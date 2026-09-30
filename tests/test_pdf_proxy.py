from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch
import asyncio
import os
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "papermind"))
import pdf_proxy
from fastapi import HTTPException
from fastapi.testclient import TestClient


class FakeResponse:
    def __init__(self, status=200, headers=None, chunks=None):
        self.status = status
        self.headers = headers or [(b"content-type", b"application/pdf")]
        self.chunks = chunks if chunks is not None else [b"%PDF-1.7\nfixture"]

    async def aiter_stream(self):
        for chunk in self.chunks:
            yield chunk


class FakePool:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    @asynccontextmanager
    async def stream(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        yield next(self.responses)


class ProxyPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_authorities_and_ambiguous_urls_are_rejected_before_connect(self):
        urls = ["http://example.org/a", "https://127.0.0.1/a", "https://169.254.169.254/",
                "https://[::1]/", "https://[::ffff:127.0.0.1]/", "https://10.2.3.4/",
                "https://localhost./", "https://example.org:8443/a", "https://u:p@example.org/a",
                "https://example.org/a#fragment", "https://example.org\\@127.0.0.1/",
                "https://example.org/\n", "https://[fe80::1%25en0]/"]
        with patch.object(pdf_proxy, "new_pool") as pool:
            for url in urls:
                with self.subTest(url=url), self.assertRaises(HTTPException) as caught:
                    await pdf_proxy.fetch_public_pdf(url)
                self.assertEqual(caught.exception.status_code, 400)
            pool.assert_not_called()

    async def test_mixed_dns_and_numeric_alias_are_denied(self):
        loop = asyncio.get_running_loop()
        for ips in (["8.8.8.8", "127.0.0.1"], ["127.0.0.1"], ["224.0.0.1"]):
            answers = [(2, 1, 6, "", (ip, 443)) for ip in ips]
            with patch.object(loop, "getaddrinfo", AsyncMock(return_value=answers)):
                with self.assertRaises(pdf_proxy.UnsafeTarget):
                    await pdf_proxy.resolve_public("2130706433", 443)

    async def test_tcp_uses_checked_ip_not_second_dns_lookup(self):
        backend = pdf_proxy.PublicNetworkBackend()
        connector = AsyncMock(return_value=object())
        with patch.object(pdf_proxy, "resolve_public", AsyncMock(return_value=["8.8.8.8"])) as resolver, \
             patch.object(backend.backend, "connect_tcp", connector):
            await backend.connect_tcp("paper.example.org", 443, timeout=5)
            resolver.assert_awaited_once_with("paper.example.org", 443)
            self.assertEqual(connector.await_args.args, ("8.8.8.8", 443))
        with patch.object(pdf_proxy, "resolve_public", AsyncMock(side_effect=pdf_proxy.UnsafeTarget("private"))), \
             patch.object(backend.backend, "connect_tcp", connector):
            connector.reset_mock()
            with self.assertRaises(pdf_proxy.UnsafeTarget):
                await backend.connect_tcp("rebound.example.org", 443)
            connector.assert_not_called()

    async def test_redirect_policy_applies_to_actual_get_not_a_separate_head(self):
        for location in ("http://example.org/a", "https://127.0.0.1/a", "https://[::1]/"):
            pool = FakePool([FakeResponse(302, [(b"location", location.encode())])])
            with patch.object(pdf_proxy, "new_pool", return_value=pool):
                with self.assertRaises(HTTPException) as caught:
                    await pdf_proxy.fetch_public_pdf("https://example.org/a")
            self.assertEqual(caught.exception.status_code, 400)
            self.assertEqual(len(pool.calls), 1)
            self.assertEqual(pool.calls[0][0], "GET")

    async def test_public_pdf_relative_redirect_and_response_cleanup(self):
        pool = FakePool([FakeResponse(302, [(b"location", b"/file.pdf")]), FakeResponse()])
        with patch.object(pdf_proxy, "new_pool", return_value=pool):
            response = await pdf_proxy.fetch_public_pdf("https://example.org/start")
        path = Path(response.path)
        self.assertTrue(path.read_bytes().startswith(b"%PDF-"))
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(pool.calls[1][1], "https://example.org/file.pdf")
        self.assertNotIn("Cookie", pool.calls[0][2]["headers"])
        self.assertNotIn("X-User-ID", pool.calls[0][2]["headers"])
        await response({"type": "http", "method": "GET", "headers": []}, AsyncMock(), AsyncMock())
        self.assertFalse(path.exists())

    async def test_disconnect_also_removes_completed_spool(self):
        with patch.object(pdf_proxy, "new_pool", return_value=FakePool([FakeResponse()])):
            response = await pdf_proxy.fetch_public_pdf("https://example.org/a")
        with self.assertRaises(ConnectionError):
            await response({"type": "http", "method": "GET", "headers": []}, AsyncMock(), AsyncMock(side_effect=ConnectionError("disconnect")))
        self.assertFalse(Path(response.path).exists())

    async def test_download_slots_remain_held_until_spool_delivery_finishes(self):
        responses = []
        try:
            for _ in range(4):
                with patch.object(pdf_proxy, "new_pool", return_value=FakePool([FakeResponse()])):
                    responses.append(await pdf_proxy.fetch_public_pdf("https://example.org/a"))
            with self.assertRaises(HTTPException) as caught:
                await pdf_proxy.fetch_public_pdf("https://example.org/a")
            self.assertEqual(caught.exception.status_code, 503)
        finally:
            for response in responses:
                await response({"type": "http", "method": "GET", "headers": []}, AsyncMock(), AsyncMock())
        with patch.object(pdf_proxy, "new_pool", return_value=FakePool([FakeResponse()])):
            response = await pdf_proxy.fetch_public_pdf("https://example.org/a")
        await response({"type": "http", "method": "GET", "headers": []}, AsyncMock(), AsyncMock())

    async def test_html_landing_page_never_redirects_authenticated_browser(self):
        pool = FakePool([FakeResponse(headers=[(b"content-type", b"text/html")])])
        with patch.object(pdf_proxy, "new_pool", return_value=pool):
            with self.assertRaises(HTTPException) as caught:
                await pdf_proxy.fetch_public_pdf("https://example.org/article")
        self.assertEqual(caught.exception.status_code, 415)
        self.assertIsNone(caught.exception.headers)

    async def test_limits_and_malformed_files_leave_no_partial_file(self):
        cases = [
            (FakeResponse(headers=[(b"content-type", b"application/pdf"), (b"content-length", b"999")]), 413),
            (FakeResponse(chunks=[b"%PDF-1.7", b"x" * 40]), 413),
            (FakeResponse(chunks=[b"not a PDF"]), 502),
            (FakeResponse(500), 502),
            (FakeResponse(headers=[(b"content-type", b"application/pdf"), (b"content-length", b"-1")]), 413),
            (FakeResponse(headers=[(b"content-type", b"application/pdf"), (b"content-length", b"bad")]), 502),
            (FakeResponse(headers=[(b"content-type", b"application/pdf"), (b"content-encoding", b"gzip")]), 415),
            (FakeResponse(headers=[(b"content-type", b"application/pdf"), (b"content-length", b"29")]), 502),
        ]
        with tempfile.TemporaryDirectory() as directory, patch.object(tempfile, "tempdir", directory):
            for fake, expected in cases:
                with patch.object(pdf_proxy, "new_pool", return_value=FakePool([fake])):
                    with self.assertRaises(HTTPException) as caught:
                        await pdf_proxy.fetch_public_pdf("https://example.org/a", max_bytes=30)
                self.assertEqual(caught.exception.status_code, expected)
                self.assertEqual(os.listdir(directory), [])

    async def test_redirect_loop_is_bounded(self):
        pool = FakePool([FakeResponse(302, [(b"location", b"/loop")]) for _ in range(6)])
        with patch.object(pdf_proxy, "new_pool", return_value=pool):
            with self.assertRaises(HTTPException) as caught:
                await pdf_proxy.fetch_public_pdf("https://example.org/a")
        self.assertEqual(caught.exception.status_code, 502)
        self.assertEqual(len(pool.calls), 6)

    async def test_timeout_and_busy_limit_have_clean_error_contracts(self):
        with patch.object(pdf_proxy, "_download", AsyncMock(side_effect=asyncio.TimeoutError)):
            with self.assertRaises(HTTPException) as caught:
                await pdf_proxy.fetch_public_pdf("https://example.org/a")
        self.assertEqual(caught.exception.status_code, 502)
        for _ in range(4):
            pdf_proxy._downloads.acquire()
        try:
            with self.assertRaises(HTTPException) as caught:
                await pdf_proxy.fetch_public_pdf("https://example.org/a")
            self.assertEqual(caught.exception.status_code, 503)
        finally:
            for _ in range(4):
                pdf_proxy._downloads.release()


class ProxyRouteTests(unittest.TestCase):
    def test_actual_route_rejects_private_url_and_serves_public_fixture(self):
        import api
        client = TestClient(api.app)
        headers = {"X-User-ID": "11111111-1111-4111-8111-111111111111"}
        self.assertEqual(client.get("/api/pdf-proxy", params={"url": "https://127.0.0.1/"}, headers=headers).status_code, 400)
        with patch.object(pdf_proxy, "new_pool", return_value=FakePool([FakeResponse()])):
            response = client.get("/api/pdf-proxy", params={"url": "https://example.org/a"}, headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b"%PDF-"))
