"""SM-005 quota and pacing, and SM-001's `match` at the provider level.

No database: these pin the pieces `catalog_lookup` is built from, with a
fake clock and recorded headers instead of sleeping or calling DigiKey.
The MCP-level acceptance tests are in `test_mcp_catalog.py`.

Acceptance points (docs: edaflow `modules/stockmanager.md`):
  SM-005.2  quota object, `header` vs `local-count`
  SM-005.3  reserve refusal
  SM-005.4  pacing: a call within the spacing is refused (provider.pacing)
  SM-005.5  throttled DigiKey 403 -> 10-minute breaker per credential,
            `suspended`, never "no match"; other 403s are credential errors
  SM-001.2  keyword fallback -> `match: "fuzzy"`
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import date

import httpx
import pytest

import app.domain.parts.providers.digikey as dk
import app.domain.parts.services.provider_cache as cache_mod
from app.domain.parts.providers.base import match_kind
from app.domain.parts.providers.digikey import DigiKeyProvider
from app.domain.parts.providers.mouser import MouserProvider
from app.domain.parts.services.provider_quota import (
    QUOTA,
    PacingRefused,
    ProviderQuota,
    QuotaReserveReached,
    account_key,
)
from app.domain.parts.services.provider_refresh_failures import looks_like_quota
from app.domain.provider_errors import ProviderSuspendedError, ProviderUpstreamError

CLIENT_ID = "fake-client-id"
ACCOUNT = account_key(CLIENT_ID)


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now



def _product(mpn: str) -> dict:
    return {
        "ManufacturerProductNumber": mpn,
        "Manufacturer": {"Name": "Texas Instruments"},
        "Description": {"ProductDescription": "IC OPAMP"},
        "ProductUrl": f"https://www.digikey.cz/en/products/detail/{mpn}",
    }


def _stub_token(monkeypatch) -> None:
    monkeypatch.setattr(
        dk, "_post_token", lambda cid, secret: {"access_token": "tok", "expires_in": 600}
    )


# ---------------------------------------------------------------------------
# SM-001.2 — match on the canonical result
# ---------------------------------------------------------------------------
def test_match_kind_is_exact_only_for_the_same_string():
    assert match_kind("OPA192IDBVT", " opa192idbvt ") == "exact"
    assert match_kind("98266-0897", "0982660897") == "fuzzy"
    assert match_kind("X", None) == "fuzzy"


def test_sm001_1_productdetails_hit_is_exact(monkeypatch):
    _stub_token(monkeypatch)
    monkeypatch.setattr(
        dk, "_get_product_details", lambda t, c, m: (200, {"Product": _product("OPA192IDBVT")})
    )
    out = DigiKeyProvider(CLIENT_ID, "s").lookup_mpn("OPA192IDBVT")
    assert out["found"] is True and out["match"] == "exact"


def test_sm001_2_keyword_fallback_is_fuzzy_with_the_found_mpn(monkeypatch):
    _stub_token(monkeypatch)
    monkeypatch.setattr(dk, "_get_product_details", lambda t, c, m: (404, {}))
    monkeypatch.setattr(
        dk,
        "_post_keyword_search",
        lambda t, c, k, limit=5: (200, {"Products": [_product("0982660897")]}),
    )
    out = DigiKeyProvider(CLIENT_ID, "s").lookup_mpn("98266-0897")
    assert out["found"] is True, "found keeps its old meaning"
    assert out["match"] == "fuzzy"
    assert out["result"]["mpn"] == "0982660897"


def test_mouser_partial_match_is_fuzzy(monkeypatch):
    payload = {
        "Errors": [],
        "SearchResults": {
            "Parts": [{"ManufacturerPartNumber": "LM358DR2G", "Manufacturer": "onsemi"}]
        },
    }
    monkeypatch.setattr("app.domain.parts.providers.mouser._post_mouser", lambda u, b: payload)
    out = MouserProvider("k").lookup_mpn("LM358")
    assert out["match"] == "fuzzy"


# ---------------------------------------------------------------------------
# SM-005.5 — DigiKey 403: throttled -> suspension, anything else -> credentials
# ---------------------------------------------------------------------------
_THROTTLED = {"ErrorMessage": "Your application has been throttled; burst limit exceeded"}


def test_sm005_5_throttled_403_is_suspended_not_no_match(monkeypatch):
    _stub_token(monkeypatch)
    monkeypatch.setattr(dk, "_get_product_details", lambda t, c, m: (403, _THROTTLED))
    out = DigiKeyProvider(CLIENT_ID, "s").lookup_mpn("OPA192IDBVT")
    assert out["found"] is False
    assert out["suspended"] is True
    assert "no match" not in out["message"]


def test_a_bare_403_is_a_credential_error_not_a_suspension(monkeypatch):
    """Unsubscribed or revoked credentials must not read as a 10-minute
    suspension — nor as "no match"."""
    _stub_token(monkeypatch)
    monkeypatch.setattr(
        dk, "_get_product_details",
        lambda t, c, m: (403, {"ErrorMessage": "The client is not authorized"}),
    )
    out = DigiKeyProvider(CLIENT_ID, "s").lookup_mpn("OPA192IDBVT")
    assert not out.get("suspended")
    assert "credentials" in out["message"] and "no match" not in out["message"]
    assert not looks_like_quota(out["message"])


def test_a_bare_403_trips_only_the_ordinary_breaker(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(cache_mod, "_clock", clock)
    _stub_token(monkeypatch)
    monkeypatch.setattr(dk, "_get_product_details", lambda t, c, m: (403, {}))
    provider = DigiKeyProvider(CLIENT_ID, "s")
    for i in range(cache_mod._CB_FAIL_THRESHOLD):
        cache_mod.lookup_with_cache(provider, f"M{i}")
    assert cache_mod.suspended_for("digikey", ACCOUNT) == 0
    with pytest.raises(ProviderUpstreamError) as exc:
        cache_mod.lookup_with_cache(provider, "NEXT")
    assert not isinstance(exc.value, ProviderSuspendedError)


@pytest.mark.parametrize("status,body,flag", [
    (403, _THROTTLED, "suspended"),
    (429, {}, None),
])
def test_sm005_5_keyword_fallback_refusal_is_not_no_match(monkeypatch, status, body, flag):
    """Before SM-005 a 403/429 from the fallback fell through to 'no match'."""
    _stub_token(monkeypatch)
    monkeypatch.setattr(dk, "_get_product_details", lambda t, c, m: (404, {}))
    monkeypatch.setattr(dk, "_post_keyword_search", lambda t, c, k, limit=5: (status, body))
    out = DigiKeyProvider(CLIENT_ID, "s").lookup_mpn("OPA192IDBVT")
    assert "no match" not in out["message"]
    assert looks_like_quota(out["message"]), "the provider-refresh job must halt on it"
    if flag:
        assert out[flag] is True


def test_sm005_5_retry_after_header_on_the_real_seam_means_suspended(monkeypatch):
    """The seam carries a 403's Retry-After into the body; an empty body
    with that header is still the burst suspension."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, headers={"Retry-After": "600"})

    @contextmanager
    def fake_client(*, provider_name, timeout):
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            yield client

    monkeypatch.setattr(dk, "make_retrying_client", fake_client)
    _stub_token(monkeypatch)
    out = DigiKeyProvider(CLIENT_ID, "s").lookup_mpn("OPA192IDBVT")
    assert out["suspended"] is True


def test_sm005_5_403_opens_the_breaker_for_ten_minutes(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(cache_mod, "_clock", clock)
    _stub_token(monkeypatch)
    calls: list[str] = []

    def details(t, c, m):
        calls.append(m)
        return (403, _THROTTLED)

    monkeypatch.setattr(dk, "_get_product_details", details)
    provider = DigiKeyProvider(CLIENT_ID, "s")

    first = cache_mod.lookup_with_cache(provider, "OPA192IDBVT")
    assert first["suspended"] is True
    assert cache_mod.suspended_for("digikey", ACCOUNT) == 600

    clock.now += 599
    with pytest.raises(ProviderSuspendedError) as exc:
        cache_mod.lookup_with_cache(provider, "OTHER-MPN")
    assert exc.value.retry_after == 1
    assert "suspended" in exc.value.message and looks_like_quota(exc.value.message)
    assert isinstance(exc.value, ProviderUpstreamError), "old handlers still catch it"
    assert calls == ["OPA192IDBVT"], "no call reached DigiKey while suspended"

    clock.now += 2
    monkeypatch.setattr(dk, "_get_product_details", lambda t, c, m: (200, {"Product": _product(m)}))
    assert cache_mod.lookup_with_cache(provider, "OTHER-MPN")["found"] is True


def test_one_credential_suspended_does_not_stop_another(monkeypatch):
    """Cross-tenant: workspace A's suspension must not deny workspace B,
    which uses different DigiKey credentials."""
    clock = FakeClock()
    monkeypatch.setattr(cache_mod, "_clock", clock)
    _stub_token(monkeypatch)

    def details(t, client_id, m):
        if client_id == "client-a":
            return (403, _THROTTLED)
        return (200, {"Product": _product(m)})

    monkeypatch.setattr(dk, "_get_product_details", details)
    a = DigiKeyProvider("client-a", "s")
    b = DigiKeyProvider("client-b", "s")
    assert cache_mod.lookup_with_cache(a, "MPN-A")["suspended"] is True
    with pytest.raises(ProviderSuspendedError):
        cache_mod.lookup_with_cache(a, "MPN-B")
    assert cache_mod.suspended_for("digikey", account_key("client-b")) == 0
    assert cache_mod.lookup_with_cache(b, "MPN-B")["found"] is True


def test_suspension_also_stops_lookup_fresh(monkeypatch):
    """The refresh path must not hammer a suspended provider either."""
    clock = FakeClock()
    monkeypatch.setattr(cache_mod, "_clock", clock)
    cache_mod._get_breaker("digikey", ACCOUNT).suspend(600)
    with pytest.raises(ProviderSuspendedError):
        cache_mod.lookup_fresh(DigiKeyProvider(CLIENT_ID, "s"), "X")


# ---------------------------------------------------------------------------
# SM-005.2 — remaining from the header, else a local count
# ---------------------------------------------------------------------------
def test_sm005_2_local_count_until_a_header_is_seen():
    q = ProviderQuota(today=lambda: date(2026, 11, 24))
    q.note_call("digikey", ACCOUNT)
    q.note_call("digikey", ACCOUNT)
    snap = q.snapshot("digikey", ACCOUNT, spent=1)
    assert snap == {
        "provider": "digikey",
        "spent": 1,
        "remaining_day": 998,
        "reserve": 200,
        "source": "local-count",
    }


def test_sm005_2_header_wins_and_later_calls_count_against_it():
    q = ProviderQuota(today=lambda: date(2026, 11, 24))
    q.note_call("digikey", ACCOUNT)
    q.note_remaining("digikey", ACCOUNT, {"X-RateLimit-Remaining": "640"})
    assert q.status("digikey", ACCOUNT).remaining_day == 640
    q.note_call("digikey", ACCOUNT)  # a call whose response carried no header
    status = q.status("digikey", ACCOUNT)
    assert (status.remaining_day, status.source) == (639, "header")


def test_a_header_from_yesterday_is_not_trusted_today():
    day = {"d": date(2026, 11, 24)}
    q = ProviderQuota(today=lambda: day["d"])
    q.note_remaining("digikey", ACCOUNT, {"x-ratelimit-remaining": "5"})
    day["d"] = date(2026, 11, 25)
    status = q.status("digikey", ACCOUNT)
    assert (status.remaining_day, status.source) == (1000, "local-count")


def test_garbage_header_is_ignored():
    q = ProviderQuota()
    q.note_remaining("digikey", ACCOUNT, {"X-RateLimit-Remaining": "lots"})
    assert q.status("digikey", ACCOUNT).source == "local-count"


def test_sm005_2_the_real_seam_records_the_header(monkeypatch):
    """The side channel works on the unstubbed seam, which still returns
    a plain `(status, body)` 2-tuple."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"Product": _product("OPA192IDBVT")},
            headers={"X-RateLimit-Remaining": "412"},
        )

    @contextmanager
    def fake_client(*, provider_name, timeout):
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            yield client

    monkeypatch.setattr(dk, "make_retrying_client", fake_client)
    status, body = dk._get_product_details("tok", CLIENT_ID, "OPA192IDBVT")
    assert status == 200 and body["Product"]
    snap = QUOTA.snapshot("digikey", ACCOUNT, spent=0)
    assert (snap["remaining_day"], snap["source"]) == (412, "header")


def test_accounts_are_kept_apart_and_never_hold_the_credential():
    q = ProviderQuota()
    q.note_call("digikey", account_key("client-a"))
    assert q.calls_today("digikey", account_key("client-b")) == 0
    assert "client-a" not in account_key("client-a")


def test_mouser_lookup_is_counted_locally(monkeypatch):
    monkeypatch.setattr(
        "app.domain.parts.providers.mouser._post_mouser",
        lambda u, b: {"Errors": [], "SearchResults": {"Parts": []}},
    )
    MouserProvider("mouser-key").lookup_mpn("X")
    assert QUOTA.calls_today("mouser", account_key("mouser-key")) == 1


# ---------------------------------------------------------------------------
# SM-005.3 — the reserve
# ---------------------------------------------------------------------------
def test_sm005_3_below_the_reserve_is_refused():
    q = ProviderQuota()
    q.note_remaining("digikey", ACCOUNT, {"X-RateLimit-Remaining": "199"})
    with pytest.raises(QuotaReserveReached) as exc:
        q.check_reserve("digikey", ACCOUNT)
    assert exc.value.status.remaining_day == 199
    assert "reserve of 200" in str(exc.value)


def test_sm005_3_at_the_reserve_is_still_allowed():
    q = ProviderQuota()
    q.note_remaining("digikey", ACCOUNT, {"X-RateLimit-Remaining": "200"})
    assert q.check_reserve("digikey", ACCOUNT).remaining_day == 200


def test_sm005_3_reserve_is_configurable(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings(), "PROVIDER_AGENT_QUOTA_RESERVE", 50)
    q = ProviderQuota()
    q.note_remaining("digikey", ACCOUNT, {"X-RateLimit-Remaining": "60"})
    assert q.check_reserve("digikey", ACCOUNT).reserve == 50


# ---------------------------------------------------------------------------
# SM-005.4 — pacing
# ---------------------------------------------------------------------------
def test_sm005_4_a_second_call_within_the_spacing_is_refused():
    clock = FakeClock()
    q = ProviderQuota(clock=clock)
    ws = uuid.uuid4()
    q.pace(ws, "digikey")
    clock.now += 0.3
    with pytest.raises(PacingRefused) as exc:
        q.pace(ws, "digikey")
    assert exc.value.retry_after_ms == 700
    assert "retry_after_ms=700" in str(exc.value)


def test_sm005_4_allowed_calls_are_at_least_the_spacing_apart():
    """Retrying after retry_after_ms always lands on an allowed slot, and
    every allowed call is >= the spacing after the previous one."""
    clock = FakeClock()
    q = ProviderQuota(clock=clock)
    ws = uuid.uuid4()
    allowed = []
    for _ in range(20):
        try:
            q.pace(ws, "digikey")
            allowed.append(clock.now)
            clock.now += 0.25
        except PacingRefused as exc:
            clock.now += exc.retry_after_ms / 1000
    gaps = [b - a for a, b in zip(allowed, allowed[1:])]
    assert len(allowed) > 3 and all(gap >= 1.0 for gap in gaps), gaps


def test_refused_call_does_not_push_the_slot_back():
    clock = FakeClock()
    q = ProviderQuota(clock=clock)
    ws = uuid.uuid4()
    q.pace(ws, "digikey")
    for _ in range(3):
        clock.now += 0.2
        with pytest.raises(PacingRefused):
            q.pace(ws, "digikey")
    clock.now += 0.4  # 1.0 s after the first, allowed call
    q.pace(ws, "digikey")


def test_sm005_4_pacing_is_per_workspace_and_provider():
    clock = FakeClock()
    q = ProviderQuota(clock=clock)
    ws = uuid.uuid4()
    q.pace(ws, "digikey")
    q.pace(ws, "mouser")
    q.pace(uuid.uuid4(), "digikey")  # none of these raise


def test_sm005_4_spacing_is_configurable(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings(), "PROVIDER_AGENT_MIN_SPACING_SECONDS", 2.5)
    clock = FakeClock()
    q = ProviderQuota(clock=clock)
    ws = uuid.uuid4()
    q.pace(ws, "digikey")
    with pytest.raises(PacingRefused) as exc:
        q.pace(ws, "digikey")
    assert exc.value.retry_after_ms == 2500


def test_pacing_never_sleeps(monkeypatch):
    import time as _time

    monkeypatch.setattr(_time, "sleep", lambda s: pytest.fail("pace() slept"))
    q = ProviderQuota()
    ws = uuid.uuid4()
    q.pace(ws, "digikey")
    with pytest.raises(PacingRefused):
        q.pace(ws, "digikey")
