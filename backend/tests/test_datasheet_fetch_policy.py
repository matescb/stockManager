"""SSRF regression tests for the relaxed datasheet fetch policy (ADR-0033).

ADR-0033 removes the host allow-list for `kind="datasheet"` — and ONLY for
that kind. Every test here exists because the allow-list used to be the
outermost guard and no longer is, so the controls behind it have to be
pinned individually:

- a URL resolving to a private / loopback / link-local / metadata address is
  refused, for both IPv4 and IPv6, including IPv4-mapped IPv6;
- a resolver answer containing even ONE non-public address is refused whole;
- DNS rebinding cannot reach a private address, because the request is issued
  against the address we validated rather than the hostname (one resolution,
  not two);
- a 30x is a refusal, never followed;
- an oversized body aborts mid-stream;
- a non-PDF payload is refused rather than stored as opaque bytes;
- plain HTTP is refused on the unrestricted path;
- credentials embedded in the URL are refused;
- `image` assets still require the allow-list — the blast radius did not
  widen beyond datasheets;
- the per-host throttle actually gates repeat requests to one vendor.
"""
from __future__ import annotations

import socket
import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.core.config import settings
from app.domain.parts.services import assets

# Real prod-shaped hosts: none of these are on the Mouser/DigiKey allow-list.
_VENDOR_URL = "https://www.vishay.com/docs/12345/example.pdf"
_PDF_BODY = b"%PDF-1.7\n" + b"datasheet-bytes" * 8
_PUBLIC_IP = "93.184.216.34"


@pytest.fixture(autouse=True)
def _isolated_upload_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(settings(), "UPLOAD_DIR", str(tmp_path), raising=False)
    assets.reset_host_throttle()
    yield
    assets.reset_host_throttle()


def _resolver(*addresses: str):
    """A `socket.getaddrinfo` stand-in returning exactly `addresses`."""

    def _fake(host, port, *args, **kwargs):
        infos = []
        for address in addresses:
            family = socket.AF_INET6 if ":" in address else socket.AF_INET
            sockaddr = (
                (address, port, 0, 0) if family == socket.AF_INET6 else (address, port)
            )
            infos.append((family, socket.SOCK_STREAM, 6, "", sockaddr))
        return infos

    return _fake


def _pdf_response(
    status_code: int = 200,
    body: bytes | None = _PDF_BODY,
    content_type: str = "application/pdf",
) -> assets._AssetResponse:
    return assets._AssetResponse(
        status_code=status_code,
        headers={"content-type": content_type},
        body=body,
    )


def _ws() -> str:
    return str(uuid.uuid4())


def _fetch(url: str, kind: str = "datasheet"):
    """Call the fetch with the ADR-0033 opt-in the cron backfill uses.

    The unrestricted policy needs BOTH `kind="datasheet"` and an explicit
    `allow_any_host=True`; request-path callers pass neither. Tests that
    assert the *default* (allow-listed) behaviour call `fetch_asset`
    directly instead of going through here.
    """
    return assets.fetch_asset(url, _ws(), kind, allow_any_host=True)


# ---------------------------------------------------------------------------
# The point of the change: manufacturer domains now work.
# ---------------------------------------------------------------------------


def test_manufacturer_host_is_fetched_without_allow_list(monkeypatch):
    """vishay.com is not — and will never be — on the allow-list."""
    monkeypatch.setattr(assets.socket, "getaddrinfo", _resolver(_PUBLIC_IP))
    monkeypatch.setattr(assets, "_http_get", lambda _t: _pdf_response())

    result = _fetch(_VENDOR_URL)

    assert result.failure_code is None
    assert result.stored is not None
    assert result.stored.ext == "pdf"
    assert result.stored.mime_type == "application/pdf"
    assert result.stored.size_bytes == len(_PDF_BODY)
    assert result.stored.storage_key.endswith(f"{result.stored.sha256}.pdf")


def test_image_from_same_host_is_still_refused(monkeypatch):
    """The relaxation is datasheet-only — images keep the allow-list."""
    monkeypatch.setattr(assets.socket, "getaddrinfo", _resolver(_PUBLIC_IP))
    called = {"hit": False}

    def _spy(_target):  # pragma: no cover - must never run
        called["hit"] = True
        return _pdf_response()

    monkeypatch.setattr(assets, "_http_get", _spy)

    result = assets.fetch_asset("https://www.vishay.com/img.png", _ws(), "image")

    assert result.stored is None
    assert result.failure_code == "host_not_allowed"
    assert called["hit"] is False, "must short-circuit before any HTTP GET"


# ---------------------------------------------------------------------------
# IP validation — the control the allow-list used to sit in front of.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "address",
    [
        "10.0.0.5",  # RFC1918
        "192.168.1.10",  # RFC1918
        "172.16.4.4",  # RFC1918
        "127.0.0.1",  # loopback
        "169.254.169.254",  # AWS/GCP metadata (link-local)
        "0.0.0.0",  # unspecified
        "::1",  # IPv6 loopback
        "fd00::1",  # IPv6 unique-local
        "fe80::1",  # IPv6 link-local
        "::ffff:169.254.169.254",  # IPv4-mapped metadata address
        "::ffff:10.0.0.5",  # IPv4-mapped RFC1918
    ],
)
def test_non_public_resolution_is_refused(monkeypatch, address):
    monkeypatch.setattr(assets.socket, "getaddrinfo", _resolver(address))
    called = {"hit": False}

    def _spy(_target):  # pragma: no cover - must never run
        called["hit"] = True
        return _pdf_response()

    monkeypatch.setattr(assets, "_http_get", _spy)

    result = _fetch(_VENDOR_URL)

    assert result.stored is None
    assert result.failure_code == "ip_not_public", address
    assert called["hit"] is False, f"opened a connection to {address}"


def test_mixed_answer_with_one_private_address_is_refused(monkeypatch):
    """Rejecting on ANY non-public address, not ALL.

    A hostile resolver that returns `[public, 127.0.0.1]` must not be able to
    get the private entry into the answer set at all — happy-eyeballs or a
    future connection retry could otherwise pick the second one.
    """
    monkeypatch.setattr(
        assets.socket, "getaddrinfo", _resolver(_PUBLIC_IP, "127.0.0.1")
    )
    monkeypatch.setattr(assets, "_http_get", lambda _t: _pdf_response())

    result = _fetch(_VENDOR_URL)

    assert result.stored is None
    assert result.failure_code == "ip_not_public"


def test_dns_failure_is_refused(monkeypatch):
    def _boom(*_args, **_kwargs):
        raise socket.gaierror("no such host")

    monkeypatch.setattr(assets.socket, "getaddrinfo", _boom)
    result = _fetch(_VENDOR_URL)
    assert result.failure_code == "ip_not_public"


# ---------------------------------------------------------------------------
# DNS rebinding — resolve once, connect to THAT address.
# ---------------------------------------------------------------------------


def _capture_httpx_stream():
    """MagicMock httpx.Client that records the stream() call and 200s."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "application/pdf"}
    mock_resp.iter_bytes = lambda chunk_size=65536: iter([_PDF_BODY])

    mock_stream_ctx = MagicMock()
    mock_stream_ctx.__enter__ = lambda _s: mock_resp
    mock_stream_ctx.__exit__ = MagicMock(return_value=False)

    mock_client = MagicMock()
    mock_client.stream.return_value = mock_stream_ctx
    mock_client.__enter__ = lambda _s: mock_client
    mock_client.__exit__ = MagicMock(return_value=False)
    return mock_client


def test_request_is_issued_against_the_validated_ip_not_the_hostname(monkeypatch):
    """The pinned-IP mechanism, asserted at the httpx boundary.

    This is what closes the rebinding window: the URL handed to httpx carries
    the IP literal, so httpx never performs a second resolution that could
    disagree with the one we validated. `Host:` and the `sni_hostname`
    extension carry the real name so vhost routing and — critically — TLS
    certificate hostname verification still work.
    """
    monkeypatch.setattr(assets.socket, "getaddrinfo", _resolver(_PUBLIC_IP))
    mock_client = _capture_httpx_stream()

    with patch(
        "app.domain.parts.services.assets.httpx.Client", return_value=mock_client
    ):
        result = _fetch(_VENDOR_URL)

    assert result.stored is not None
    (method, request_url), kwargs = mock_client.stream.call_args
    assert method == "GET"
    assert request_url == f"https://{_PUBLIC_IP}/docs/12345/example.pdf"
    assert "vishay.com" not in request_url, "hostname must not reach the connector"
    assert kwargs["headers"]["Host"] == "www.vishay.com"
    assert kwargs["extensions"]["sni_hostname"] == "www.vishay.com"


def test_rebinding_second_answer_is_never_used(monkeypatch):
    """A resolver that flips public → private between calls cannot win.

    The old code resolved for the check and let httpx resolve again for the
    connect. This fake models exactly that attacker: first answer public,
    every later answer loopback. Since we resolve once and connect to the
    literal, the connection still goes to the public address — and, crucially,
    getaddrinfo is called exactly once.
    """
    answers = [_PUBLIC_IP, "127.0.0.1", "127.0.0.1"]
    calls = {"n": 0}

    def _flipping(host, port, *args, **kwargs):
        index = min(calls["n"], len(answers) - 1)
        calls["n"] += 1
        return _resolver(answers[index])(host, port)

    monkeypatch.setattr(assets.socket, "getaddrinfo", _flipping)
    mock_client = _capture_httpx_stream()

    with patch(
        "app.domain.parts.services.assets.httpx.Client", return_value=mock_client
    ):
        result = _fetch(_VENDOR_URL)

    assert result.stored is not None
    assert calls["n"] == 1, "hostname must be resolved exactly once"
    (_method, request_url), _kwargs = mock_client.stream.call_args
    assert request_url.startswith(f"https://{_PUBLIC_IP}/")
    assert "127.0.0.1" not in request_url


def test_query_string_and_port_survive_pinning(monkeypatch):
    monkeypatch.setattr(assets.socket, "getaddrinfo", _resolver(_PUBLIC_IP))
    mock_client = _capture_httpx_stream()

    with patch(
        "app.domain.parts.services.assets.httpx.Client", return_value=mock_client
    ):
        _fetch("https://docs.rs-online.com:8443/pdf/a.pdf?sig=abc&v=2")

    (_method, request_url), kwargs = mock_client.stream.call_args
    assert request_url == f"https://{_PUBLIC_IP}:8443/pdf/a.pdf?sig=abc&v=2"
    assert kwargs["headers"]["Host"] == "docs.rs-online.com:8443"


def test_ipv6_literal_is_bracketed(monkeypatch):
    monkeypatch.setattr(assets.socket, "getaddrinfo", _resolver("2606:2800:220:1::1"))
    mock_client = _capture_httpx_stream()

    with patch(
        "app.domain.parts.services.assets.httpx.Client", return_value=mock_client
    ):
        _fetch(_VENDOR_URL)

    (_method, request_url), _kwargs = mock_client.stream.call_args
    assert request_url.startswith("https://[2606:2800:220:1::1]/")


# ---------------------------------------------------------------------------
# Scheme / credential / redirect / size / type refusals.
# ---------------------------------------------------------------------------


def test_plain_http_is_refused_on_the_unrestricted_path(monkeypatch):
    called = {"hit": False}

    def _spy(*_args, **_kwargs):  # pragma: no cover - must never run
        called["hit"] = True
        return []

    monkeypatch.setattr(assets.socket, "getaddrinfo", _spy)
    result = _fetch("http://www.vishay.com/docs/1.pdf")
    assert result.failure_code == "scheme_not_https"
    assert called["hit"] is False, "must refuse before resolving"


def test_credentials_in_url_are_refused(monkeypatch):
    monkeypatch.setattr(assets.socket, "getaddrinfo", _resolver(_PUBLIC_IP))
    result = _fetch("https://user:secret@www.vishay.com/docs/1.pdf")
    assert result.failure_code == "credentials_in_url"


@pytest.mark.parametrize("status_code", [301, 302, 303, 307, 308])
def test_redirect_is_refused_not_followed(monkeypatch, status_code):
    monkeypatch.setattr(assets.socket, "getaddrinfo", _resolver(_PUBLIC_IP))
    monkeypatch.setattr(
        assets,
        "_http_get",
        lambda _t: _pdf_response(status_code=status_code, body=b"", content_type="text/html"),
    )
    result = _fetch(_VENDOR_URL)
    assert result.stored is None
    assert result.failure_code == "redirect_refused"


def test_http_client_never_follows_redirects(monkeypatch):
    """Pin the constructor flag itself, not just the caller's handling."""
    monkeypatch.setattr(assets.socket, "getaddrinfo", _resolver(_PUBLIC_IP))
    mock_client = _capture_httpx_stream()

    with patch(
        "app.domain.parts.services.assets.httpx.Client", return_value=mock_client
    ) as client_cls:
        _fetch(_VENDOR_URL)

    assert client_cls.call_args.kwargs["follow_redirects"] is False
    # ADR-0008: TLS verification is never disabled on this path.
    assert "verify" not in client_cls.call_args.kwargs


def test_oversized_body_aborts_mid_stream(monkeypatch):
    """The streaming guard must abandon the iterator once past the cap."""
    monkeypatch.setattr(assets.socket, "getaddrinfo", _resolver(_PUBLIC_IP))

    chunks_consumed: list[int] = []
    chunk_a = b"%PDF-1.7" + b"a" * (assets._MAX_BYTES // 2)
    chunk_b = b"b" * (assets._MAX_BYTES // 2 + 1)
    chunk_c = b"c" * 1024  # must never be read

    def _iter_bytes(chunk_size=65536):
        for chunk in (chunk_a, chunk_b, chunk_c):
            chunks_consumed.append(len(chunk))
            yield chunk

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "application/pdf"}
    mock_resp.iter_bytes = _iter_bytes

    mock_stream_ctx = MagicMock()
    mock_stream_ctx.__enter__ = lambda _s: mock_resp
    mock_stream_ctx.__exit__ = MagicMock(return_value=False)

    mock_client = MagicMock()
    mock_client.stream.return_value = mock_stream_ctx
    mock_client.__enter__ = lambda _s: mock_client
    mock_client.__exit__ = MagicMock(return_value=False)

    with patch(
        "app.domain.parts.services.assets.httpx.Client", return_value=mock_client
    ):
        result = _fetch(_VENDOR_URL)

    assert result.stored is None
    assert result.failure_code == "too_large"
    assert chunks_consumed == [len(chunk_a), len(chunk_b)], (
        f"streaming guard read past the cap: {chunks_consumed!r}"
    )


def test_content_length_pre_check_refuses_before_reading(monkeypatch):
    monkeypatch.setattr(assets.socket, "getaddrinfo", _resolver(_PUBLIC_IP))
    monkeypatch.setattr(
        assets,
        "_http_get",
        lambda _t: assets._AssetResponse(
            status_code=200,
            headers={
                "content-type": "application/pdf",
                "content-length": str(assets._MAX_BYTES + 1),
            },
            body=None,
        ),
    )
    result = _fetch(_VENDOR_URL)
    assert result.failure_code == "too_large"


def test_html_landing_page_is_refused_not_stored(monkeypatch):
    """A vendor URL that 200s with HTML must not fill the store with junk."""
    monkeypatch.setattr(assets.socket, "getaddrinfo", _resolver(_PUBLIC_IP))
    monkeypatch.setattr(
        assets,
        "_http_get",
        lambda _t: _pdf_response(
            body=b"<!doctype html><html>not a datasheet</html>",
            content_type="text/html; charset=utf-8",
        ),
    )
    result = _fetch("https://www.ti.com/product/OPA333")
    assert result.stored is None
    assert result.failure_code == "unexpected_type"


def test_html_body_declared_as_pdf_is_refused_by_magic_bytes(monkeypatch):
    monkeypatch.setattr(assets.socket, "getaddrinfo", _resolver(_PUBLIC_IP))
    monkeypatch.setattr(
        assets,
        "_http_get",
        lambda _t: _pdf_response(body=b"<!doctype html><html></html>"),
    )
    result = _fetch(_VENDOR_URL)
    assert result.stored is None
    assert result.failure_code == "magic_mismatch"


def test_svg_declared_as_datasheet_is_refused(monkeypatch):
    monkeypatch.setattr(assets.socket, "getaddrinfo", _resolver(_PUBLIC_IP))
    monkeypatch.setattr(
        assets,
        "_http_get",
        lambda _t: _pdf_response(
            body=b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
            content_type="image/svg+xml",
        ),
    )
    result = _fetch(_VENDOR_URL)
    assert result.stored is None
    assert result.failure_code == "unexpected_type"


def test_upstream_404_reports_the_status(monkeypatch):
    monkeypatch.setattr(assets.socket, "getaddrinfo", _resolver(_PUBLIC_IP))
    monkeypatch.setattr(
        assets, "_http_get", lambda _t: _pdf_response(status_code=404, body=b"")
    )
    result = _fetch(_VENDOR_URL)
    assert result.failure_code == "http_404"


def test_timeout_reports_timeout(monkeypatch):
    import httpx

    monkeypatch.setattr(assets.socket, "getaddrinfo", _resolver(_PUBLIC_IP))

    def _boom(_target):
        raise httpx.ReadTimeout("slow vendor")

    monkeypatch.setattr(assets, "_http_get", _boom)
    result = _fetch(_VENDOR_URL)
    assert result.failure_code == "timeout"


def test_arbitrary_network_error_is_contained(monkeypatch):
    monkeypatch.setattr(assets.socket, "getaddrinfo", _resolver(_PUBLIC_IP))

    def _boom(_target):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(assets, "_http_get", _boom)
    result = _fetch(_VENDOR_URL)
    assert result.failure_code == "network_error"


@pytest.mark.parametrize(
    "url",
    ["", "file:///etc/passwd", "javascript:alert(1)", "ftp://vishay.com/x.pdf", "not-a-url"],
)
def test_non_http_urls_are_refused(url):
    result = _fetch(url)
    assert result.stored is None
    assert result.failure_code == "invalid_url"


# ---------------------------------------------------------------------------
# Per-host throttle.
# ---------------------------------------------------------------------------


def test_throttle_gates_repeat_requests_to_one_host(monkeypatch):
    """A backfill must not hammer a single vendor."""
    monkeypatch.setattr(
        settings(), "ASSET_FETCH_MIN_HOST_INTERVAL_SECONDS", 5.0, raising=False
    )
    slept: list[float] = []
    clock = {"now": 1000.0}
    monkeypatch.setattr(assets.time, "monotonic", lambda: clock["now"])

    def _sleep(seconds: float) -> None:
        slept.append(seconds)
        clock["now"] += seconds

    monkeypatch.setattr(assets.time, "sleep", _sleep)

    assets._throttle_host("www.vishay.com")
    assert slept == [], "first request for a host must not wait"

    assets._throttle_host("www.vishay.com")
    assert slept == [5.0], "second request for the same host must wait the full gap"

    assets._throttle_host("www.ti.com")
    assert slept == [5.0], "a different host must not be gated by the first"


def test_throttle_disabled_at_zero(monkeypatch):
    monkeypatch.setattr(
        settings(), "ASSET_FETCH_MIN_HOST_INTERVAL_SECONDS", 0, raising=False
    )
    calls: list[float] = []
    monkeypatch.setattr(assets.time, "sleep", lambda s: calls.append(s))

    assets._throttle_host("www.vishay.com")
    assets._throttle_host("www.vishay.com")

    assert calls == []


# ---------------------------------------------------------------------------
# Log hygiene.
# ---------------------------------------------------------------------------


def test_redaction_drops_query_string_and_credentials():
    redacted = assets._redact("https://u:p@vendor.example.com/dl/x.pdf?token=SECRET")
    assert redacted == "https://vendor.example.com/dl/x.pdf"
    assert "SECRET" not in redacted
    assert "u:p" not in redacted


def test_httpx_honours_our_host_header_and_sni_extension():
    """Pin the httpx contract the pinned-IP mechanism depends on.

    Two things must hold, and both are library behaviour we do not control:
    a caller-supplied `Host` header must REPLACE the one httpx derives from
    the URL (otherwise every vhost 404s), and `sni_hostname` must survive
    into `request.extensions` (otherwise TLS would verify the certificate
    against the IP literal). An httpx upgrade that breaks either one should
    fail here, not in prod.
    """
    import httpx

    with httpx.Client() as client:
        request = client.build_request(
            "GET",
            f"https://{_PUBLIC_IP}/docs/x.pdf",
            headers={"Host": "www.vishay.com"},
            extensions={"sni_hostname": "www.vishay.com"},
        )

    assert request.url.host == _PUBLIC_IP
    assert request.headers.get_list("host") == ["www.vishay.com"], (
        "httpx must not send both the derived and the supplied Host header"
    )
    assert request.extensions["sni_hostname"] == "www.vishay.com"


def test_datasheet_without_the_opt_in_still_requires_the_allow_list(monkeypatch):
    """The relaxation needs BOTH the kind AND an explicit caller opt-in.

    `kind="datasheet"` alone is not enough. This is what keeps the widened
    surface out of the request path: provider import and provider refresh
    call `fetch_provider_asset`, which never opts in, so a user-triggered
    request can neither reach a non-allow-listed host nor be made to block
    the single uvicorn worker (ADR-0012) on an arbitrary vendor.
    """
    monkeypatch.setattr(assets.socket, "getaddrinfo", _resolver(_PUBLIC_IP))
    called = {"hit": False}

    def _spy(_target):  # pragma: no cover - must never run
        called["hit"] = True
        return _pdf_response()

    monkeypatch.setattr(assets, "_http_get", _spy)

    result = assets.fetch_asset(_VENDOR_URL, _ws(), "datasheet")

    assert result.stored is None
    assert result.failure_code == "host_not_allowed"
    assert called["hit"] is False


def test_request_path_helper_never_opts_in(monkeypatch):
    """`fetch_provider_asset` is what the routes call. Pin its policy."""
    monkeypatch.setattr(assets.socket, "getaddrinfo", _resolver(_PUBLIC_IP))
    monkeypatch.setattr(assets, "_http_get", lambda _t: _pdf_response())

    assert (
        assets.fetch_provider_asset(_VENDOR_URL, _ws(), "datasheet") is None
    ), "the request-path helper must keep the Mouser/DigiKey allow-list"

    # ...and still works for an allow-listed host.
    assert (
        assets.fetch_provider_asset(
            "https://media.digikey.com/pdf/a.pdf", _ws(), "datasheet"
        )
        is not None
    )


def test_opt_in_does_not_relax_non_datasheet_kinds(monkeypatch):
    """Passing the flag for an `image` must change nothing."""
    monkeypatch.setattr(assets.socket, "getaddrinfo", _resolver(_PUBLIC_IP))
    monkeypatch.setattr(assets, "_http_get", lambda _t: _pdf_response())

    result = assets.fetch_asset(
        "https://www.vishay.com/img.png", _ws(), "image", allow_any_host=True
    )

    assert result.stored is None
    assert result.failure_code == "host_not_allowed"
