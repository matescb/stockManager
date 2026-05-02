"""BE2-018 / issue #63 — more_available flag in search response data.

When a bucket returns more than _BUCKET_LIMIT (25) rows, the response
must surface ``data.more_available: True`` inside the API envelope.
"""
from __future__ import annotations

from tests._factories import create_part, signup_user


def test_search_more_available_true_when_parts_exceed_bucket_limit(client):
    signup_user(client)
    prefix = "searchable-prefix"
    # Create 26 parts — one more than _BUCKET_LIMIT (25).
    for i in range(26):
        create_part(client, name=f"{prefix}-part-{i:03d}")

    r = client.get("/api/search", params={"q": prefix})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["more_available"] is True
    # The response must not exceed _BUCKET_LIMIT entries in the parts bucket.
    assert len(data["parts"]) <= 25


def test_search_more_available_false_when_results_fit(client):
    signup_user(client)
    prefix = "small-result-set"
    for i in range(3):
        create_part(client, name=f"{prefix}-part-{i}")

    r = client.get("/api/search", params={"q": prefix})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["more_available"] is False
    assert len(data["parts"]) == 3


def test_search_more_available_in_data_not_toplevel(client):
    """more_available must be inside data, not at the top-level envelope."""
    signup_user(client)
    r = client.get("/api/search", params={"q": "anything"})
    assert r.status_code == 200, r.text
    body = r.json()
    # Top-level keys are only data and status (API envelope invariant).
    assert "more_available" not in body
    assert "more_available" in body["data"]
