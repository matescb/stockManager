"""Unit-level pin for the typed envelope helpers (CQ-007 / issue #123).

The runtime shape contract is already covered by `test_envelope.py`
(TEST-008 / #110). This file only asserts that the new `Envelope[T]`
typing aid in `app.core.responses` doesn't mutate the runtime payload —
the FE depends on the exact `{data, status}` keys, so any future tweak
of the helpers must keep this true.
"""
from __future__ import annotations

from app.core.responses import Envelope, ErrorEnvelope, Status, err, ok


def test_ok_envelope_runtime_shape():
    body = ok({"id": "abc"})
    assert body["data"] == {"id": "abc"}
    assert body["status"]["category"] == "ok"
    assert body["status"]["message"] == "OK"
    assert set(body.keys()) == {"data", "status"}


def test_ok_envelope_none_payload():
    body = ok(None, "logged out")
    assert body["data"] is None
    assert body["status"]["message"] == "logged out"


def test_ok_envelope_with_message():
    body = ok({"k": 1}, "all good")
    assert body["data"] == {"k": 1}
    assert body["status"]["message"] == "all good"


def test_err_envelope_runtime_shape():
    body = err("conflict", "duplicate mpn")
    assert body["data"] is None
    assert body["status"]["category"] == "conflict"
    assert body["status"]["message"] == "duplicate mpn"
    assert set(body.keys()) == {"data", "status"}


def test_err_envelope_with_errors_list():
    body = err("validation_error", "bad", errors=[{"field": "x", "message": "missing"}])
    assert body["errors"] == [{"field": "x", "message": "missing"}]


def test_status_typed_dict_keys():
    """`Status` must keep `{category, message}` exactly. Other keys
    would silently change the FE contract."""
    # `Status` is a TypedDict, so its `__required_keys__` is the full
    # set of fields. Pin both keys.
    assert Status.__required_keys__ == frozenset({"category", "message"})


def test_envelope_typed_dict_keys():
    assert Envelope.__required_keys__ == frozenset({"data", "status"})


def test_error_envelope_is_open_dict():
    """`ErrorEnvelope` must be a plain `dict[str, Any]` alias so the
    http exception handler can spread `existing_id`/etc. onto the top
    level without tripping a strict schema."""
    assert getattr(ErrorEnvelope, "__origin__", None) is dict
