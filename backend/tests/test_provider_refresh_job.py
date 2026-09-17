"""The `provider-refresh` operator job — re-ask every provider a part is
linked to, and write down what came back.

The route refreshes one part from one provider. This sweeps a whole
workspace, and prod is 285 linked parts across 290 links, of which 252
go through the SECONDARY path. What is pinned here:

* **a dry run writes nothing.** The provider lookups are real — doing
  them is what makes the CSV a plan rather than a guess — but every DB
  write lands in a savepoint that is rolled back and no asset is
  downloaded, because a file in `UPLOAD_DIR` is the one thing a rollback
  cannot take back.
* **the tier rules survive the sweep.** A primary-linked part gets its
  columns driven; a secondary-linked one gets canonical specs and its
  own namespaced catalog keys and NOT ONE part column.
* **the `spec-normalize` backfill is not undone.** Archived junk stays
  archived and `custom_fields.provider` stamps stay put.
* **quota is a stop, not a crash.** A rate-limited provider ends the
  sweep at exit 3 with everything finished so far committed and the CSV
  on disk; any other per-part failure is one CSV row and the sweep
  carries on.
* **workspace isolation.** `--workspace` scopes the run, and one
  workspace's credentials are never used to look up another's parts.
"""
from __future__ import annotations

import csv
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.cli.run_job import JOBS, JobOptions, main, run_job
from app.core.advisory_locks import PROVIDER_REFRESH_LOCK_CLASSID
from app.core.time import utcnow
from app.domain.audit.models import AuditLog
from app.domain.custom_fields.models import CustomField
from app.domain.parts.models import Part, PartProviderLink
from app.domain.parts.services import provider_refresh_job as job_module
from app.domain.parts.services.provider_refresh_job import (
    ACTION_ERROR,
    ACTION_LINKED,
    ACTION_MISS,
    ACTION_REFRESHED,
    ACTION_SKIPPED,
    AUDIT_ACTION,
    JOB_NAME,
    REPORT_COLUMNS,
    ProviderQuotaExhausted,
    SweepAlreadyRunning,
    UnknownWorkspaceError,
    refresh_linked_parts,
)
from app.domain.workspaces.models import Workspace
from app.main import app
from tests._factories import signup_user

MPN = "RC0402FR-0710KL"
OTHER_MPN = "GRM188R71C104KA01D"


# ---------------------------------------------------------------------------
# Stub providers
#
# The job is monkeypatched at `make_provider`, not at the HTTP seam: what
# is under test is which provider gets asked about which part and what
# happens to the answer, and a scripted client says that in one line
# where a vendor payload says it in thirty.
# ---------------------------------------------------------------------------
class StubProvider:
    """A `PartsProvider` whose answers the test writes.

    `answers` maps an MPN to the canonical record shape (or to an
    exception instance to raise, or to None for a clean miss). Every
    lookup is recorded so a test can assert the sweep asked once per
    (part, provider).
    """

    def __init__(self, name: str, answers: dict[str, object]) -> None:
        self.name = name
        self.answers = answers
        self.calls: list[str] = []

    def lookup_mpn(self, mpn: str) -> dict:
        self.calls.append(mpn)
        answer = self.answers.get(mpn, None)
        if isinstance(answer, Exception):
            raise answer
        if answer is None:
            return {"found": False, "result": None, "message": "no match for MPN"}
        if isinstance(answer, str):
            # A `found: False` whose message is the interesting part —
            # this is how DigiKey reports a 429.
            return {"found": False, "result": None, "message": answer}
        return {"found": True, "result": answer, "message": None}


def _record(mpn: str, *, manufacturer: str, category: str = "Resistors", **extra) -> dict:
    record = {
        "mpn": mpn,
        "manufacturer": manufacturer,
        "description": f"{manufacturer} {mpn}",
        "category": category,
        "footprint": "0402",
        "datasheet_url": None,
        "image_url": None,
        "source_url": f"https://example.com/{manufacturer}/{mpn}",
        "specs": [
            {"key": "Resistance", "value": "10 kOhms"},
            {"key": "Tolerance", "value": "±1%"},
        ],
    }
    record.update(extra)
    return record


@pytest.fixture
def providers(monkeypatch) -> dict[str, StubProvider]:
    """The registry `make_provider` answers from, plus a clean cache.

    `provider_cache` keeps a process-global circuit breaker and result
    cache. Five consecutive failures open the breaker for 60 seconds,
    which would leak from the error tests into every test that ran after
    them in the same process.
    """
    from app.domain.parts.services import provider_cache

    provider_cache._breakers.clear()
    provider_cache._cache._store.clear()

    registry: dict[str, StubProvider] = {}

    def fake_make_provider(name, api_key, api_secret=None):
        # Same contract as the real factory: unconfigured is None, not a
        # client that fails later.
        if not name or name == "none" or not api_key:
            return None
        return registry.get(name)

    monkeypatch.setattr(
        "app.domain.parts.services.provider_refresh.make_provider", fake_make_provider
    )
    yield registry
    provider_cache._breakers.clear()
    provider_cache._cache._store.clear()


@pytest.fixture(autouse=True)
def slept(monkeypatch) -> list[float]:
    """Every sleep the throttle asked for, and none of them taken.

    Autouse because the throttle is a blocking sleep and no test should
    pay 750 ms for it; requestable by name because two tests assert on
    the list.
    """
    calls: list[float] = []
    monkeypatch.setattr(job_module.time, "sleep", calls.append)
    return calls


# ---------------------------------------------------------------------------
# Workspace / part fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def client(db) -> TestClient:
    return TestClient(app)


def _signup(client: TestClient, email: str | None = None) -> uuid.UUID:
    return uuid.UUID(signup_user(client, email=email).json()["data"]["workspace_id"])


def _set_primary(db, ws_id: uuid.UUID, provider: str, key: str = "k") -> None:
    from app.core.secrets import encrypt

    ws = db.get(Workspace, ws_id)
    ws.parts_provider = provider
    ws.parts_provider_api_key = encrypt(key)
    ws.parts_provider_api_secret = encrypt("s")
    db.flush()


def _add_secondary(db, ws_id: uuid.UUID, provider: str, key: str = "k2") -> None:
    from app.domain.parts.provider_credentials import upsert

    upsert(db, ws=db.get(Workspace, ws_id), user_id=None, provider=provider, api_key=key)
    db.flush()


def _category(client: TestClient, name: str) -> str:
    r = client.post("/api/categories", json={"name": name})
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]["id"]


def _part(
    client: TestClient,
    db,
    mpn: str = MPN,
    *,
    linked_provider: str | None = None,
    links: tuple[str, ...] = (),
    **kwargs,
) -> uuid.UUID:
    """A part through the API, then linked through the ORM.

    Neither `linked_provider` nor a `part_provider_links` row is writable
    over REST — only an import or a refresh creates them — so the fixture
    writes them directly to model a catalogue the sweep will find.
    """
    r = client.post(
        "/api/parts",
        json={"name": mpn or "unlinked", "part_type": "linked", "mpn": mpn, **kwargs},
    )
    assert r.status_code in (200, 201), r.text
    part_id = uuid.UUID(r.json()["data"]["id"])
    part = db.get(Part, part_id)
    if linked_provider is not None:
        part.linked_provider = linked_provider
    for provider in links:
        db.add(
            PartProviderLink(
                workspace_id=part.workspace_id,
                part_id=part_id,
                provider=provider,
                external_id=mpn,
            )
        )
    db.flush()
    return part_id


def _rows(db, part_id: uuid.UUID) -> dict[str, CustomField]:
    db.expire_all()
    return {
        row.key: row
        for row in db.execute(
            select(CustomField)
            .where(CustomField.object_type == "part")
            .where(CustomField.object_id == part_id)
        ).scalars()
    }


def _report_rows(path: Path) -> list[dict[str, str]]:
    """The change section of the CSV, as dicts. The summary sections come
    after a blank line and are read by `_summary_rows`."""
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    assert tuple(rows[0]) == REPORT_COLUMNS
    out = []
    for row in rows[1:]:
        if not row:
            break
        out.append(dict(zip(REPORT_COLUMNS, row)))
    return out


def _summary_rows(path: Path, marker: str) -> list[list[str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return [row for row in csv.reader(handle) if row and row[0] == marker]


@pytest.fixture
def primary_workspace(client: TestClient, db, providers) -> tuple[uuid.UUID, uuid.UUID]:
    """Mouser primary, one Mouser-linked resistor, filed under Resistors."""
    ws_id = _signup(client)
    _set_primary(db, ws_id, "mouser")
    providers["mouser"] = StubProvider(
        "mouser", {MPN: _record(MPN, manufacturer="Yageo")}
    )
    part_id = _part(client, db, MPN, linked_provider="mouser", links=("mouser",))
    db.commit()
    return ws_id, part_id


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------
def test_a_dry_run_writes_nothing_to_the_database(
    primary_workspace, db, tmp_path: Path, providers
) -> None:
    ws_id, part_id = primary_workspace
    part = db.get(Part, part_id)
    before_columns = (part.manufacturer, part.description, part.footprint)
    before_rows = {key: row.value for key, row in _rows(db, part_id).items()}

    outcome = _sweep(db, tmp_path / "dry.csv")

    assert outcome.applied is False
    db.expire_all()
    part = db.get(Part, part_id)
    assert (part.manufacturer, part.description, part.footprint) == before_columns
    assert {key: row.value for key, row in _rows(db, part_id).items()} == before_rows


def test_a_dry_run_still_calls_the_provider_and_writes_the_csv(
    primary_workspace, db, tmp_path: Path, providers
) -> None:
    """The lookups are reads, and doing them for real is what makes the
    CSV a plan rather than a guess."""
    _, part_id = primary_workspace
    report = tmp_path / "dry.csv"

    _sweep(db, report)

    assert providers["mouser"].calls == [MPN]
    rows = _report_rows(report)
    assert [row["action"] for row in rows] == [ACTION_REFRESHED]
    assert rows[0]["part_id"] == str(part_id)
    assert rows[0]["tier"] == "primary"
    # `resistance`, `tolerance` and the primary's bare `source_url`.
    assert rows[0]["specs_added"] == "3"


# ---------------------------------------------------------------------------
# Apply — the primary tier
# ---------------------------------------------------------------------------
def test_apply_drives_the_part_columns_and_writes_canonical_specs(
    primary_workspace, db, tmp_path: Path, providers
) -> None:
    ws_id, part_id = primary_workspace

    outcome = _sweep(db, tmp_path / "apply.csv", apply=True)

    assert outcome.applied is True
    db.expire_all()
    part = db.get(Part, part_id)
    assert part.manufacturer == "Yageo"
    assert part.footprint == "0402"
    assert part.linked_provider == "mouser"
    rows = _rows(db, part_id)
    # Canonical, un-namespaced, stamped with who wrote them (ADR-0034).
    assert rows["resistance"].value == "10 kΩ"
    assert rows["resistance"].provider == "mouser"
    assert rows["tolerance"].provider == "mouser"


def test_apply_files_an_uncategorized_part_but_never_re_files_one(
    client: TestClient, db, tmp_path: Path, providers
) -> None:
    """`apply_provider_category` only ever fills a NULL `category_id`. The
    118 prod parts with no category are the cheapest win here; a category
    a user chose is a decision the vendor taxonomy may not overrule."""
    ws_id = _signup(client)
    _set_primary(db, ws_id, "mouser")
    providers["mouser"] = StubProvider(
        "mouser",
        {
            MPN: _record(MPN, manufacturer="Yageo"),
            OTHER_MPN: _record(OTHER_MPN, manufacturer="Murata"),
        },
    )
    resistors = _category(client, "Resistors")
    chosen = _category(client, "Bias network")
    unfiled = _part(client, db, MPN, linked_provider="mouser")
    filed = _part(client, db, OTHER_MPN, linked_provider="mouser", category_id=chosen)
    db.commit()

    _sweep(db, tmp_path / "apply.csv", apply=True)

    db.expire_all()
    assert str(db.get(Part, unfiled).category_id) == resistors
    assert str(db.get(Part, filed).category_id) == chosen


# ---------------------------------------------------------------------------
# Apply — the secondary tier
# ---------------------------------------------------------------------------
def test_a_secondary_link_writes_specs_and_namespaced_keys_but_no_column(
    client: TestClient, db, tmp_path: Path, providers
) -> None:
    """252 of prod's 290 links are DigiKey secondaries. A secondary owns
    no part column at all (ADR-0031): canonical specs un-namespaced,
    everything else behind its own prefix."""
    ws_id = _signup(client)
    _set_primary(db, ws_id, "mouser")
    _add_secondary(db, ws_id, "digikey")
    providers["digikey"] = StubProvider(
        "digikey", {MPN: _record(MPN, manufacturer="DIGIKEY-MFR")}
    )
    part_id = _part(client, db, MPN, links=("digikey",))
    part = db.get(Part, part_id)
    part.manufacturer = "Yageo"
    db.commit()

    _sweep(db, tmp_path / "apply.csv", apply=True)

    db.expire_all()
    part = db.get(Part, part_id)
    assert part.manufacturer == "Yageo", "a secondary must not touch a part column"
    assert part.linked_provider is None
    rows = _rows(db, part_id)
    assert rows["resistance"].provider == "digikey"
    assert "digikey:source_url" in rows
    assert "source_url" not in rows


def test_the_primary_is_refreshed_before_the_secondaries(
    client: TestClient, db, tmp_path: Path, providers
) -> None:
    """Precedence decides who wins a contested canonical key, but the
    report reads as a sequence and the primary is the tier that owns the
    part's columns — it goes first so a reviewer sees the columns move
    before the rows that depend on the category it just filed."""
    ws_id = _signup(client)
    _set_primary(db, ws_id, "mouser")
    _add_secondary(db, ws_id, "digikey")
    providers["mouser"] = StubProvider("mouser", {MPN: _record(MPN, manufacturer="Yageo")})
    providers["digikey"] = StubProvider(
        "digikey", {MPN: _record(MPN, manufacturer="DIGIKEY-MFR")}
    )
    _part(client, db, MPN, linked_provider="mouser", links=("mouser", "digikey"))
    db.commit()
    report = tmp_path / "apply.csv"

    _sweep(db, report, apply=True)

    assert [row["provider"] for row in _report_rows(report)] == ["mouser", "digikey"]
    assert [row["tier"] for row in _report_rows(report)] == ["primary", "secondary"]


# ---------------------------------------------------------------------------
# --link-missing-providers
# ---------------------------------------------------------------------------
def test_link_missing_providers_links_on_a_hit(
    client: TestClient, db, tmp_path: Path, providers
) -> None:
    ws_id = _signup(client)
    _set_primary(db, ws_id, "mouser")
    _add_secondary(db, ws_id, "digikey")
    providers["mouser"] = StubProvider("mouser", {MPN: _record(MPN, manufacturer="Yageo")})
    providers["digikey"] = StubProvider(
        "digikey", {MPN: _record(MPN, manufacturer="DIGIKEY-MFR")}
    )
    part_id = _part(client, db, MPN, linked_provider="mouser", links=("mouser",))
    db.commit()
    report = tmp_path / "apply.csv"

    _sweep(db, report, apply=True, link_missing_providers=True)

    db.expire_all()
    linked = {
        row.provider
        for row in db.execute(
            select(PartProviderLink).where(PartProviderLink.part_id == part_id)
        ).scalars()
    }
    assert linked == {"mouser", "digikey"}
    actions = {row["provider"]: row["action"] for row in _report_rows(report)}
    assert actions == {"mouser": ACTION_REFRESHED, "digikey": ACTION_LINKED}


def test_link_missing_providers_records_a_miss_rather_than_an_error(
    client: TestClient, db, tmp_path: Path, providers
) -> None:
    """A provider that has never heard of the part is not a failure, and
    it is not a link either."""
    ws_id = _signup(client)
    _set_primary(db, ws_id, "mouser")
    _add_secondary(db, ws_id, "digikey")
    providers["mouser"] = StubProvider("mouser", {MPN: _record(MPN, manufacturer="Yageo")})
    providers["digikey"] = StubProvider("digikey", {})
    part_id = _part(client, db, MPN, linked_provider="mouser", links=("mouser",))
    db.commit()
    report = tmp_path / "apply.csv"

    _sweep(db, report, apply=True, link_missing_providers=True)

    db.expire_all()
    assert {
        row.provider
        for row in db.execute(
            select(PartProviderLink).where(PartProviderLink.part_id == part_id)
        ).scalars()
    } == {"mouser"}
    miss = [row for row in _report_rows(report) if row["provider"] == "digikey"][0]
    assert miss["action"] == ACTION_MISS
    assert "no match" in miss["error"]


def test_a_fuzzy_hit_is_refused_on_a_provider_the_part_is_already_linked_to(
    client: TestClient, db, tmp_path: Path, providers
) -> None:
    """The guard is unconditional in a sweep, not just for new links.

    DigiKey falls back to a keyword search when exact-match
    `ProductDetails` misses, so a "hit" on a part the sweep never asked a
    human about can be a different product — and writing its canonical
    specs would also re-file the part under that product's taxonomy.
    A reformatted MPN reads as `miss`, which is a CSV line an operator
    can act on; a wrong match is silent."""
    ws_id = _signup(client)
    _set_primary(db, ws_id, "mouser")
    providers["mouser"] = StubProvider(
        "mouser", {MPN: _record("SOMETHING-ELSE-99", manufacturer="Yageo")}
    )
    part_id = _part(client, db, MPN, linked_provider="mouser", links=("mouser",))
    db.commit()
    report = tmp_path / "apply.csv"

    _sweep(db, report, apply=True)

    db.expire_all()
    part = db.get(Part, part_id)
    assert part.mpn == MPN, "a fuzzy hit must not rewrite the MPN"
    assert part.manufacturer != "Yageo"
    assert _report_rows(report)[0]["action"] == ACTION_MISS


def test_link_missing_providers_refuses_a_fuzzy_hit(
    client: TestClient, db, tmp_path: Path, providers
) -> None:
    """DigiKey falls back to a keyword search and Mouser matches
    partially. That is fine for a part a human asked about by name; it is
    not fine here, where a near miss would link the part to a different
    product and import its specs."""
    ws_id = _signup(client)
    _set_primary(db, ws_id, "mouser")
    _add_secondary(db, ws_id, "digikey")
    providers["mouser"] = StubProvider("mouser", {MPN: _record(MPN, manufacturer="Yageo")})
    providers["digikey"] = StubProvider(
        "digikey", {MPN: _record("SOMETHING-ELSE-99", manufacturer="DIGIKEY-MFR")}
    )
    part_id = _part(client, db, MPN, linked_provider="mouser", links=("mouser",))
    db.commit()
    report = tmp_path / "apply.csv"

    _sweep(db, report, apply=True, link_missing_providers=True)

    db.expire_all()
    assert {
        row.provider
        for row in db.execute(
            select(PartProviderLink).where(PartProviderLink.part_id == part_id)
        ).scalars()
    } == {"mouser"}
    miss = [row for row in _report_rows(report) if row["provider"] == "digikey"][0]
    assert miss["action"] == ACTION_MISS


def test_link_missing_providers_never_runs_the_primary_on_a_secondary_part(
    client: TestClient, db, tmp_path: Path, providers
) -> None:
    """The flag adds SECONDARIES only.

    On icicle Mouser is primary and 252 of 290 links are DigiKey
    secondaries. Letting the flag add Mouser to those parts would run the
    PRIMARY path on each of them and rewrite manufacturer, mpn,
    footprint, description, `linked_provider` and `part_type` from a
    provider nobody chose for that part — and it is not reversible the
    way the runbook describes, because the unlink route refuses the
    primary. Promoting a provider onto a part stays a per-part human
    action through the refresh route."""
    ws_id = _signup(client)
    _set_primary(db, ws_id, "mouser")
    _add_secondary(db, ws_id, "digikey")
    providers["mouser"] = StubProvider("mouser", {MPN: _record(MPN, manufacturer="Yageo")})
    providers["digikey"] = StubProvider(
        "digikey", {MPN: _record(MPN, manufacturer="DIGIKEY-MFR")}
    )
    part_id = _part(client, db, MPN, links=("digikey",))
    part = db.get(Part, part_id)
    part.manufacturer = "ORIGINAL-MFR"
    db.commit()
    report = tmp_path / "apply.csv"

    _sweep(db, report, apply=True, link_missing_providers=True)

    assert providers["mouser"].calls == [], "the primary was not asked"
    db.expire_all()
    part = db.get(Part, part_id)
    assert part.manufacturer == "ORIGINAL-MFR"
    assert part.linked_provider is None
    assert [row["provider"] for row in _report_rows(report)] == ["digikey"]


def test_the_primary_is_ordered_first_even_when_a_secondary_is_being_added(
    client: TestClient, db, tmp_path: Path, providers
) -> None:
    """One sorted list, not linked-then-unlinked. The primary is the tier
    that fills a NULL category, and the category picks the spec schema
    every later payload is read through, so it cannot end up behind a
    secondary the flag happened to append."""
    ws_id = _signup(client)
    _set_primary(db, ws_id, "mouser")
    _add_secondary(db, ws_id, "digikey")
    providers["mouser"] = StubProvider("mouser", {MPN: _record(MPN, manufacturer="Yageo")})
    providers["digikey"] = StubProvider(
        "digikey", {MPN: _record(MPN, manufacturer="DIGIKEY-MFR")}
    )
    # Linked to the SECONDARY only, so a naive "linked first, then the
    # flag's additions" order would put digikey ahead of mouser.
    _part(client, db, MPN, linked_provider="mouser", links=("digikey",))
    db.commit()
    report = tmp_path / "apply.csv"

    _sweep(db, report, apply=True, link_missing_providers=True)

    assert [row["provider"] for row in _report_rows(report)] == ["mouser", "digikey"]


def test_without_the_flag_a_credentialed_provider_is_not_asked(
    client: TestClient, db, tmp_path: Path, providers
) -> None:
    ws_id = _signup(client)
    _set_primary(db, ws_id, "mouser")
    _add_secondary(db, ws_id, "digikey")
    providers["mouser"] = StubProvider("mouser", {MPN: _record(MPN, manufacturer="Yageo")})
    providers["digikey"] = StubProvider(
        "digikey", {MPN: _record(MPN, manufacturer="DIGIKEY-MFR")}
    )
    _part(client, db, MPN, linked_provider="mouser", links=("mouser",))
    db.commit()

    _sweep(db, tmp_path / "apply.csv", apply=True)

    assert providers["digikey"].calls == []


# ---------------------------------------------------------------------------
# What the sweep must NOT undo — the spec-normalize backfill
# ---------------------------------------------------------------------------
def test_archived_junk_stays_archived_and_provider_stamps_survive(
    client: TestClient, db, tmp_path: Path, providers
) -> None:
    """`spec-normalize` ran on prod the day this job was written. A
    refresh that resurrected the 277 ECCN rows it retired, or blanked the
    `provider` stamps it filled, would undo the whole backfill."""
    ws_id = _signup(client)
    _set_primary(db, ws_id, "mouser")
    providers["mouser"] = StubProvider("mouser", {MPN: _record(MPN, manufacturer="Yageo")})
    part_id = _part(client, db, MPN, linked_provider="mouser", links=("mouser",))
    archived = CustomField(
        workspace_id=ws_id,
        object_type="part",
        object_id=part_id,
        key="ECCN",
        value="EAR99",
        source="provider",
        provider="mouser",
        archived_at=utcnow(),
    )
    db.add(archived)
    db.commit()

    _sweep(db, tmp_path / "apply.csv", apply=True)

    db.expire_all()
    rows = _rows(db, part_id)
    assert rows["ECCN"].archived_at is not None, "a retired junk key must stay retired"
    assert rows["resistance"].provider == "mouser"


def test_a_manual_row_is_never_overwritten(
    client: TestClient, db, tmp_path: Path, providers
) -> None:
    ws_id = _signup(client)
    _set_primary(db, ws_id, "mouser")
    providers["mouser"] = StubProvider("mouser", {MPN: _record(MPN, manufacturer="Yageo")})
    part_id = _part(client, db, MPN, linked_provider="mouser", links=("mouser",))
    db.add(
        CustomField(
            workspace_id=ws_id,
            object_type="part",
            object_id=part_id,
            key="resistance",
            value="hand-measured",
            source="manual",
        )
    )
    db.commit()

    _sweep(db, tmp_path / "apply.csv", apply=True)

    db.expire_all()
    assert _rows(db, part_id)["resistance"].value == "hand-measured"


def test_a_second_apply_changes_nothing(
    primary_workspace, db, tmp_path: Path, providers
) -> None:
    """Idempotent over unchanged upstream data. `last_refresh_at` and the
    link's own timestamp move on every run by design — they are the
    record that the run happened — so the claim is about the part columns
    and the spec rows."""
    ws_id, part_id = primary_workspace
    _sweep(db, tmp_path / "first.csv", apply=True)
    db.expire_all()
    part = db.get(Part, part_id)
    before_columns = (part.manufacturer, part.mpn, part.footprint, part.description)
    before_rows = {
        key: (row.value, row.value_num, row.provider, row.archived_at)
        for key, row in _rows(db, part_id).items()
    }

    _sweep(db, tmp_path / "second.csv", apply=True)

    db.expire_all()
    part = db.get(Part, part_id)
    assert (part.manufacturer, part.mpn, part.footprint, part.description) == before_columns
    assert {
        key: (row.value, row.value_num, row.provider, row.archived_at)
        for key, row in _rows(db, part_id).items()
    } == before_rows


# ---------------------------------------------------------------------------
# Assets — the one side effect a savepoint cannot take back
# ---------------------------------------------------------------------------
def _with_assets(mpn: str) -> dict:
    return _record(
        mpn,
        manufacturer="Yageo",
        image_url="https://example.com/i.jpg",
        datasheet_url="https://example.com/d.pdf",
    )


def test_a_dry_run_downloads_no_assets_and_reports_what_it_would_pull(
    client: TestClient, db, tmp_path: Path, providers, monkeypatch
) -> None:
    """A downloaded file lands in UPLOAD_DIR, outside the savepoint the
    batch rolls back. A dry run that fetched would leave orphans behind
    and spend one real HTTP request per asset to learn what the payload
    already told it."""
    fetched: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "app.domain.parts.services.provider_refresh.fetch_provider_asset",
        lambda url, ws_id, kind: fetched.append((url, kind)) or "/api/parts/assets/x",
    )
    ws_id = _signup(client)
    _set_primary(db, ws_id, "mouser")
    providers["mouser"] = StubProvider("mouser", {MPN: _with_assets(MPN)})
    _part(client, db, MPN, linked_provider="mouser", links=("mouser",))
    db.commit()
    report = tmp_path / "dry.csv"

    _sweep(db, report)

    assert fetched == [], "a dry run must not download"
    row = _report_rows(report)[0]
    assert row["assets_fetched"] == ""
    assert set(row["assets_would_fetch"].split()) == {"image", "datasheet"}


def test_apply_downloads_the_assets_and_reports_them(
    client: TestClient, db, tmp_path: Path, providers, monkeypatch
) -> None:
    fetched: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "app.domain.parts.services.provider_refresh.fetch_provider_asset",
        lambda url, ws_id, kind: fetched.append((url, kind)) or "/api/parts/assets/x",
    )
    ws_id = _signup(client)
    _set_primary(db, ws_id, "mouser")
    providers["mouser"] = StubProvider("mouser", {MPN: _with_assets(MPN)})
    _part(client, db, MPN, linked_provider="mouser", links=("mouser",))
    db.commit()
    report = tmp_path / "apply.csv"

    _sweep(db, report, apply=True)

    assert {kind for _, kind in fetched} == {"image", "datasheet"}
    row = _report_rows(report)[0]
    assert set(row["assets_fetched"].split()) == {"image", "datasheet"}
    assert row["assets_would_fetch"] == ""


def test_a_secondary_never_touches_either_asset_column(
    client: TestClient, db, tmp_path: Path, providers, monkeypatch
) -> None:
    """ADR-0031: the primary owns the part's files."""
    monkeypatch.setattr(
        "app.domain.parts.services.provider_refresh.fetch_provider_asset",
        lambda url, ws_id, kind: pytest.fail("a secondary must not download"),
    )
    ws_id = _signup(client)
    _set_primary(db, ws_id, "mouser")
    _add_secondary(db, ws_id, "digikey")
    providers["digikey"] = StubProvider("digikey", {MPN: _with_assets(MPN)})
    _part(client, db, MPN, links=("digikey",))
    db.commit()
    report = tmp_path / "apply.csv"

    _sweep(db, report, apply=True)

    row = _report_rows(report)[0]
    assert (row["assets_fetched"], row["assets_would_fetch"]) == ("", "")


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------
def test_an_unlinked_part_is_out_of_scope(
    client: TestClient, db, tmp_path: Path, providers
) -> None:
    """35 prod parts have neither a link row nor `linked_provider`. By
    default this job only re-asks the providers a part is already known
    to; `--include-unlinked` is what widens the scope to those, and
    `--link-missing-providers` widens what a LINKED part is asked."""
    ws_id = _signup(client)
    _set_primary(db, ws_id, "mouser")
    providers["mouser"] = StubProvider("mouser", {MPN: _record(MPN, manufacturer="Yageo")})
    _part(client, db, MPN)
    db.commit()

    outcome = _sweep(db, tmp_path / "dry.csv")

    assert outcome.parts == 0
    assert providers["mouser"].calls == []


def test_a_part_with_no_mpn_is_out_of_scope(
    client: TestClient, db, tmp_path: Path, providers
) -> None:
    ws_id = _signup(client)
    _set_primary(db, ws_id, "mouser")
    providers["mouser"] = StubProvider("mouser", {})
    part_id = _part(client, db, MPN, linked_provider="mouser", links=("mouser",))
    db.get(Part, part_id).mpn = "   "
    db.commit()

    outcome = _sweep(db, tmp_path / "dry.csv")

    assert outcome.parts == 0


def test_an_archived_part_is_out_of_scope(
    primary_workspace, db, tmp_path: Path, providers
) -> None:
    _, part_id = primary_workspace
    db.get(Part, part_id).archived_at = utcnow()
    db.commit()

    assert _sweep(db, tmp_path / "dry.csv").parts == 0


def test_only_uncategorized_narrows_the_sweep(
    client: TestClient, db, tmp_path: Path, providers
) -> None:
    """The cheapest way to file the 118 prod parts that have no category
    without spending quota on the 211 that do."""
    ws_id = _signup(client)
    _set_primary(db, ws_id, "mouser")
    providers["mouser"] = StubProvider(
        "mouser",
        {
            MPN: _record(MPN, manufacturer="Yageo"),
            OTHER_MPN: _record(OTHER_MPN, manufacturer="Murata"),
        },
    )
    filed_category = _category(client, "Resistors")
    _part(client, db, MPN, linked_provider="mouser", category_id=filed_category)
    unfiled = _part(client, db, OTHER_MPN, linked_provider="mouser")
    db.commit()
    report = tmp_path / "dry.csv"

    outcome = _sweep(db, report, only_uncategorized=True)

    assert outcome.parts == 1
    assert [row["part_id"] for row in _report_rows(report)] == [str(unfiled)]


def test_limit_caps_the_number_of_parts(
    client: TestClient, db, tmp_path: Path, providers
) -> None:
    ws_id = _signup(client)
    _set_primary(db, ws_id, "mouser")
    providers["mouser"] = StubProvider(
        "mouser",
        {
            MPN: _record(MPN, manufacturer="Yageo"),
            OTHER_MPN: _record(OTHER_MPN, manufacturer="Murata"),
        },
    )
    _part(client, db, MPN, linked_provider="mouser")
    _part(client, db, OTHER_MPN, linked_provider="mouser")
    db.commit()

    assert _sweep(db, tmp_path / "dry.csv", limit=1).parts == 1


# ---------------------------------------------------------------------------
# --include-unlinked
#
# 34 prod parts carry an MPN and no provider link at all; one carries
# neither. They were typed in or imported from a BOM, and nothing has ever
# asked a vendor about them. This is the ONE case where the primary tier
# may run on a part it has never owned — nobody owns its columns yet — so
# every test here is also a test that it fills gaps rather than
# overwriting what somebody typed.
# ---------------------------------------------------------------------------
def _local_part(client: TestClient, db, mpn: str | None = MPN, **kwargs) -> uuid.UUID:
    """A part with no link row, no `linked_provider` and `part_type=local`."""
    return _part(client, db, mpn, part_type="local", **kwargs)


def test_include_unlinked_lets_the_primary_claim_a_local_part(
    client: TestClient, db, tmp_path: Path, providers
) -> None:
    """The claim is the whole point: a link row, the `linked_provider`
    column, the derived `part_type`, a category it did not have and the
    canonical specs. Reported as `linked`, because the provider had no
    claim on the part at all."""
    ws_id = _signup(client)
    _set_primary(db, ws_id, "mouser")
    providers["mouser"] = StubProvider(
        "mouser", {MPN: _record(MPN, manufacturer="Yageo")}
    )
    # `apply_provider_category` resolves a vendor path against the
    # workspace's own tree and creates nothing, so the category has to
    # exist for the part to be filed under it.
    resistors = _category(client, "Resistors")
    part_id = _local_part(client, db)
    db.commit()
    report = tmp_path / "apply.csv"

    _sweep(db, report, apply=True, include_unlinked=True)

    db.expire_all()
    part = db.get(Part, part_id)
    assert part.linked_provider == "mouser"
    assert part.part_type == "linked"
    assert part.manufacturer == "Yageo"
    assert str(part.category_id) == resistors, "a NULL category is filed"
    assert {
        row.provider
        for row in db.execute(
            select(PartProviderLink).where(PartProviderLink.part_id == part_id)
        ).scalars()
    } == {"mouser"}
    assert _rows(db, part_id)["resistance"].value == "10 kΩ"
    row = _report_rows(report)[0]
    assert (row["action"], row["tier"]) == (ACTION_LINKED, "primary")
    assert row["category_before"] == ""
    assert row["category_after"] == "Resistors"


def test_include_unlinked_keeps_a_description_somebody_typed(
    client: TestClient, db, tmp_path: Path, providers
) -> None:
    """The difference between claiming an unowned part and refreshing an
    owned one. On a part the primary already owns the vendor drives
    `description`; here it only fills a gap, because the operator who
    typed it never asked a vendor to replace their words."""
    ws_id = _signup(client)
    _set_primary(db, ws_id, "mouser")
    providers["mouser"] = StubProvider(
        "mouser", {MPN: _record(MPN, manufacturer="Yageo")}
    )
    part_id = _local_part(
        client,
        db,
        description="10k 0402 from the drawer by the window",
        manufacturer="ACME (relabelled)",
    )
    db.commit()

    _sweep(db, tmp_path / "apply.csv", apply=True, include_unlinked=True)

    db.expire_all()
    part = db.get(Part, part_id)
    assert part.description == "10k 0402 from the drawer by the window"
    assert part.manufacturer == "ACME (relabelled)"
    # The claim itself still happened.
    assert part.linked_provider == "mouser"


def test_include_unlinked_fills_a_description_that_is_only_the_mpn(
    client: TestClient, db, tmp_path: Path, providers
) -> None:
    """A part created from a scan carries its own MPN as its description.
    That is a placeholder, not a sentence somebody wrote, so the vendor's
    text is an improvement rather than a loss."""
    ws_id = _signup(client)
    _set_primary(db, ws_id, "mouser")
    providers["mouser"] = StubProvider(
        "mouser", {MPN: _record(MPN, manufacturer="Yageo")}
    )
    part_id = _local_part(client, db, description=MPN)
    db.commit()

    _sweep(db, tmp_path / "apply.csv", apply=True, include_unlinked=True)

    db.expire_all()
    assert db.get(Part, part_id).description == f"Yageo {MPN}"


def test_include_unlinked_respects_description_locally_edited(
    client: TestClient, db, tmp_path: Path, providers
) -> None:
    """Belt and braces with the empty-or-MPN rule: the flag that says "a
    human wrote this" is honoured even when the text happens to be the
    MPN."""
    ws_id = _signup(client)
    _set_primary(db, ws_id, "mouser")
    providers["mouser"] = StubProvider(
        "mouser", {MPN: _record(MPN, manufacturer="Yageo")}
    )
    part_id = _local_part(client, db, description=MPN)
    db.get(Part, part_id).description_locally_edited = True
    db.commit()

    _sweep(db, tmp_path / "apply.csv", apply=True, include_unlinked=True)

    db.expire_all()
    assert db.get(Part, part_id).description == MPN


def test_include_unlinked_reports_a_part_with_no_mpn_as_skipped(
    client: TestClient, db, tmp_path: Path, providers
) -> None:
    """One prod part is unlinked AND has no MPN. There is nothing to ask
    any provider about it, and it is the operator who has to supply the
    MPN — so it is a line in the CSV naming the reason, not a part
    silently left out of a run whose whole purpose was to find it."""
    ws_id = _signup(client)
    _set_primary(db, ws_id, "mouser")
    providers["mouser"] = StubProvider("mouser", {})
    part_id = _local_part(client, db, mpn=None)
    db.commit()
    report = tmp_path / "dry.csv"

    outcome = _sweep(db, report, include_unlinked=True)

    assert providers["mouser"].calls == [], "nothing to look up"
    assert outcome.parts == 1
    rows = _report_rows(report)
    assert [row["action"] for row in rows] == [ACTION_SKIPPED]
    assert rows[0]["part_id"] == str(part_id)
    assert rows[0]["provider"] == "", "no provider was asked"
    assert "no MPN" in rows[0]["error"]


def test_include_unlinked_records_a_miss_per_provider_and_writes_nothing(
    client: TestClient, db, tmp_path: Path, providers
) -> None:
    ws_id = _signup(client)
    _set_primary(db, ws_id, "mouser")
    _add_secondary(db, ws_id, "digikey")
    providers["mouser"] = StubProvider("mouser", {})
    providers["digikey"] = StubProvider("digikey", {})
    part_id = _local_part(client, db)
    db.commit()
    report = tmp_path / "apply.csv"

    _sweep(db, report, apply=True, include_unlinked=True)

    db.expire_all()
    part = db.get(Part, part_id)
    assert part.linked_provider is None
    assert part.part_type == "local"
    assert part.category_id is None
    assert _rows(db, part_id) == {}
    rows = _report_rows(report)
    assert {row["provider"]: row["action"] for row in rows} == {
        "mouser": ACTION_MISS,
        "digikey": ACTION_MISS,
    }


def test_include_unlinked_asks_every_secondary_with_credentials(
    client: TestClient, db, tmp_path: Path, providers
) -> None:
    """The primary is tried first and misses; the secondary answers and
    links as a secondary always does — no part column, its own namespace.
    """
    ws_id = _signup(client)
    _set_primary(db, ws_id, "mouser")
    _add_secondary(db, ws_id, "digikey")
    providers["mouser"] = StubProvider("mouser", {})
    providers["digikey"] = StubProvider(
        "digikey", {MPN: _record(MPN, manufacturer="DIGIKEY-MFR")}
    )
    part_id = _local_part(client, db)
    db.commit()
    report = tmp_path / "apply.csv"

    _sweep(db, report, apply=True, include_unlinked=True)

    db.expire_all()
    part = db.get(Part, part_id)
    assert part.manufacturer != "DIGIKEY-MFR", "a secondary writes no part column"
    assert part.linked_provider is None
    assert {
        row.provider
        for row in db.execute(
            select(PartProviderLink).where(PartProviderLink.part_id == part_id)
        ).scalars()
    } == {"digikey"}
    assert [row["provider"] for row in _report_rows(report)] == ["mouser", "digikey"]


def test_include_unlinked_refuses_a_fuzzy_hit(
    client: TestClient, db, tmp_path: Path, providers
) -> None:
    """Exact-MPN only, the same guard every other pair in the sweep gets.
    A near miss here would claim a local part for another product
    entirely and rewrite the columns nobody had filled yet."""
    ws_id = _signup(client)
    _set_primary(db, ws_id, "mouser")
    providers["mouser"] = StubProvider(
        "mouser", {MPN: _record("SOMETHING-ELSE-99", manufacturer="Yageo")}
    )
    part_id = _local_part(client, db)
    db.commit()
    report = tmp_path / "apply.csv"

    _sweep(db, report, apply=True, include_unlinked=True)

    db.expire_all()
    part = db.get(Part, part_id)
    assert part.linked_provider is None
    assert part.mpn == MPN
    assert _report_rows(report)[0]["action"] == ACTION_MISS


def test_include_unlinked_dry_run_writes_nothing(
    client: TestClient, db, tmp_path: Path, providers
) -> None:
    ws_id = _signup(client)
    _set_primary(db, ws_id, "mouser")
    providers["mouser"] = StubProvider(
        "mouser", {MPN: _record(MPN, manufacturer="Yageo")}
    )
    part_id = _local_part(client, db)
    db.commit()
    report = tmp_path / "dry.csv"

    outcome = _sweep(db, report, include_unlinked=True)

    assert outcome.applied is False
    db.expire_all()
    part = db.get(Part, part_id)
    assert part.linked_provider is None
    assert part.part_type == "local"
    assert part.category_id is None
    assert _rows(db, part_id) == {}
    assert db.execute(
        select(PartProviderLink).where(PartProviderLink.part_id == part_id)
    ).first() is None
    # The plan is still produced by the code that would apply it.
    assert _report_rows(report)[0]["action"] == ACTION_LINKED


def test_include_unlinked_leaves_a_secondary_owned_part_to_its_secondary(
    client: TestClient, db, tmp_path: Path, providers
) -> None:
    """The widened scope is UNLINKED parts only. A part a secondary
    already knows is not unowned, so the primary must not claim it —
    that is still the per-part human decision the runbook describes."""
    ws_id = _signup(client)
    _set_primary(db, ws_id, "mouser")
    _add_secondary(db, ws_id, "digikey")
    providers["mouser"] = StubProvider(
        "mouser", {MPN: _record(MPN, manufacturer="Yageo")}
    )
    providers["digikey"] = StubProvider(
        "digikey", {MPN: _record(MPN, manufacturer="DIGIKEY-MFR")}
    )
    part_id = _part(client, db, MPN, links=("digikey",))
    db.commit()
    report = tmp_path / "apply.csv"

    _sweep(db, report, apply=True, include_unlinked=True)

    assert providers["mouser"].calls == [], "the primary was not asked"
    db.expire_all()
    assert db.get(Part, part_id).linked_provider is None
    assert [row["provider"] for row in _report_rows(report)] == ["digikey"]


def test_include_unlinked_still_needs_an_active_part(
    client: TestClient, db, tmp_path: Path, providers
) -> None:
    ws_id = _signup(client)
    _set_primary(db, ws_id, "mouser")
    providers["mouser"] = StubProvider(
        "mouser", {MPN: _record(MPN, manufacturer="Yageo")}
    )
    part_id = _local_part(client, db)
    db.get(Part, part_id).archived_at = utcnow()
    db.commit()

    assert _sweep(db, tmp_path / "dry.csv", include_unlinked=True).parts == 0


# ---------------------------------------------------------------------------
# Workspace isolation
# ---------------------------------------------------------------------------
def test_workspace_scopes_the_run_and_never_crosses_credentials(
    client: TestClient, db, tmp_path: Path, providers
) -> None:
    """Two workspaces, two primaries. `--workspace` runs one of them, and
    the other's provider is not asked about any part — the credentials
    are resolved per workspace, from that workspace's own rows."""
    ws_a = _signup(client)
    _set_primary(db, ws_a, "mouser")
    part_a = _part(client, db, MPN, linked_provider="mouser", links=("mouser",))

    other = TestClient(app)
    ws_b = _signup(other)
    _set_primary(db, ws_b, "digikey")
    part_b = _part(other, db, OTHER_MPN, linked_provider="digikey", links=("digikey",))
    db.commit()

    providers["mouser"] = StubProvider("mouser", {MPN: _record(MPN, manufacturer="Yageo")})
    providers["digikey"] = StubProvider(
        "digikey", {OTHER_MPN: _record(OTHER_MPN, manufacturer="Murata")}
    )
    report = tmp_path / "dry.csv"

    outcome = _sweep(db, report, workspace_id=ws_a)

    assert outcome.parts == 1
    assert providers["mouser"].calls == [MPN]
    assert providers["digikey"].calls == []
    assert {row["workspace_id"] for row in _report_rows(report)} == {str(ws_a)}
    assert str(part_b) not in {row["part_id"] for row in _report_rows(report)}
    assert str(part_a) in {row["part_id"] for row in _report_rows(report)}


def test_a_run_over_every_workspace_keeps_them_apart(
    client: TestClient, db, tmp_path: Path, providers
) -> None:
    ws_a = _signup(client)
    _set_primary(db, ws_a, "mouser")
    _part(client, db, MPN, linked_provider="mouser", links=("mouser",))

    other = TestClient(app)
    ws_b = _signup(other)
    _set_primary(db, ws_b, "digikey")
    part_b = _part(other, db, OTHER_MPN, linked_provider="digikey", links=("digikey",))
    db.commit()

    providers["mouser"] = StubProvider("mouser", {MPN: _record(MPN, manufacturer="Yageo")})
    providers["digikey"] = StubProvider(
        "digikey", {OTHER_MPN: _record(OTHER_MPN, manufacturer="Murata")}
    )
    report = tmp_path / "apply.csv"

    _sweep(db, report, apply=True)

    db.expire_all()
    rows = {row["part_id"]: row for row in _report_rows(report)}
    assert rows[str(part_b)]["workspace_id"] == str(ws_b)
    assert rows[str(part_b)]["provider"] == "digikey"
    # The Murata part's specs landed in ws_b and nowhere else.
    assert all(
        row.workspace_id == ws_b for row in _rows(db, part_b).values()
    )


def test_a_workspace_that_does_not_exist_is_a_usage_error(db, tmp_path: Path) -> None:
    with pytest.raises(UnknownWorkspaceError):
        _sweep(db, tmp_path / "dry.csv", workspace_id=uuid.uuid4())


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------
def test_one_part_failing_does_not_abort_the_sweep(
    client: TestClient, db, tmp_path: Path, providers
) -> None:
    from app.domain.parts.providers.base import ProviderUpstreamError

    ws_id = _signup(client)
    _set_primary(db, ws_id, "mouser")
    providers["mouser"] = StubProvider(
        "mouser",
        {
            MPN: ProviderUpstreamError("mouser", "Mouser upstream returned HTTP 503"),
            OTHER_MPN: _record(OTHER_MPN, manufacturer="Murata", category="Capacitors"),
        },
    )
    broken = _part(client, db, MPN, linked_provider="mouser")
    good = _part(client, db, OTHER_MPN, linked_provider="mouser")
    db.commit()
    report = tmp_path / "apply.csv"

    outcome = _sweep(db, report, apply=True)

    rows = {row["part_id"]: row for row in _report_rows(report)}
    assert rows[str(broken)]["action"] == ACTION_ERROR
    assert "503" in rows[str(broken)]["error"]
    assert rows[str(good)]["action"] == ACTION_REFRESHED
    assert outcome.counts[ACTION_ERROR] == 1
    db.expire_all()
    assert db.get(Part, good).manufacturer == "Murata"


def test_a_linked_provider_with_no_credentials_is_skipped(
    client: TestClient, db, tmp_path: Path, providers
) -> None:
    """A part linked to a provider the workspace has since removed the
    key for. Not an error — there is nothing to ask."""
    ws_id = _signup(client)
    _set_primary(db, ws_id, "mouser")
    providers["mouser"] = StubProvider("mouser", {MPN: _record(MPN, manufacturer="Yageo")})
    _part(client, db, MPN, linked_provider="mouser", links=("mouser", "digikey"))
    db.commit()
    report = tmp_path / "dry.csv"

    _sweep(db, report)

    rows = {row["provider"]: row for row in _report_rows(report)}
    assert rows["digikey"]["action"] == ACTION_SKIPPED
    assert rows["mouser"]["action"] == ACTION_REFRESHED
    assert "no credentials" in rows["digikey"]["error"]


def test_a_skipped_row_still_names_the_tier_it_would_have_run_as(
    client: TestClient, db, tmp_path: Path, providers
) -> None:
    """The tier comes from the workspace's `parts_provider`, not from a
    client we could not build — otherwise a part linked to the PRIMARY
    whose key was removed would report itself as a secondary."""
    ws_id = _signup(client)
    _set_primary(db, ws_id, "mouser")
    # No stub registered for mouser, so `make_provider` hands back None.
    _part(client, db, MPN, linked_provider="mouser")
    db.commit()
    report = tmp_path / "dry.csv"

    _sweep(db, report)

    row = _report_rows(report)[0]
    assert (row["action"], row["tier"]) == (ACTION_SKIPPED, "primary")


def test_a_row_that_changed_nothing_repeats_the_category_rather_than_blanking_it(
    client: TestClient, db, tmp_path: Path, providers
) -> None:
    """A blank `category_after` next to a filled `category_before` reads
    as "the sweep cleared the category"."""
    ws_id = _signup(client)
    _set_primary(db, ws_id, "mouser")
    providers["mouser"] = StubProvider("mouser", {})
    resistors = _category(client, "Resistors")
    _part(client, db, MPN, linked_provider="mouser", category_id=resistors)
    db.commit()
    report = tmp_path / "dry.csv"

    _sweep(db, report)

    row = _report_rows(report)[0]
    assert row["action"] == ACTION_MISS
    assert row["category_before"] == row["category_after"] == "Resistors"


@pytest.mark.parametrize(
    "answer",
    [
        "DigiKey rate limit reached",
        "Too many requests",
        "Your daily quota has been exceeded",
    ],
)
def test_a_quota_answer_stops_the_sweep(
    client: TestClient, db, tmp_path: Path, providers, answer: str
) -> None:
    """DigiKey and Mouser both cap the free tier at about 1,000 calls a
    day. Burning through the rest of a 290-link sweep against a provider
    that is refusing is pointless; the run stops with what it finished."""
    ws_id = _signup(client)
    _set_primary(db, ws_id, "mouser")
    providers["mouser"] = StubProvider("mouser", {MPN: answer, OTHER_MPN: answer})
    _part(client, db, MPN, linked_provider="mouser")
    _part(client, db, OTHER_MPN, linked_provider="mouser")
    db.commit()

    with pytest.raises(ProviderQuotaExhausted):
        _sweep(db, tmp_path / "dry.csv")

    assert len(providers["mouser"].calls) == 1, "the sweep stopped at the first refusal"


def test_a_message_naming_http_429_stops_the_sweep(
    client: TestClient, db, tmp_path: Path, providers
) -> None:
    """Mouser's transport layer turns a 429 into a `ProviderUpstreamError`
    whose `status_code` is 502 — the number survives only in the text, so
    the status code alone is not enough."""
    from app.domain.parts.providers.base import ProviderUpstreamError

    ws_id = _signup(client)
    _set_primary(db, ws_id, "mouser")
    providers["mouser"] = StubProvider(
        "mouser",
        {MPN: ProviderUpstreamError("mouser", "Mouser upstream returned HTTP 429")},
    )
    _part(client, db, MPN, linked_provider="mouser")
    db.commit()

    with pytest.raises(ProviderQuotaExhausted):
        _sweep(db, tmp_path / "dry.csv")


@pytest.mark.parametrize(
    "message",
    [
        "no match for MPN SN74HC4290",
        "no match for MPN LM429",
        "DigiKey returned HTTP 4290",
    ],
)
def test_a_part_number_containing_429_is_not_read_as_a_quota_refusal(
    client: TestClient, db, tmp_path: Path, providers, message: str
) -> None:
    """A bare `429` substring would halt the sweep on an ordinary miss and
    blame a provider that is answering fine."""
    ws_id = _signup(client)
    _set_primary(db, ws_id, "mouser")
    providers["mouser"] = StubProvider(
        "mouser", {MPN: message, OTHER_MPN: _record(OTHER_MPN, manufacturer="Murata")}
    )
    _part(client, db, MPN, linked_provider="mouser")
    _part(client, db, OTHER_MPN, linked_provider="mouser")
    db.commit()

    outcome = _sweep(db, tmp_path / "dry.csv")

    assert outcome.halted_on is None
    assert outcome.parts == 2, "the sweep carried on past the miss"


def test_a_429_exception_also_stops_the_sweep(
    client: TestClient, db, tmp_path: Path, providers
) -> None:
    from app.domain.provider_errors import ProviderRateLimitError

    ws_id = _signup(client)
    _set_primary(db, ws_id, "mouser")
    providers["mouser"] = StubProvider(
        "mouser",
        {
            MPN: ProviderRateLimitError("mouser", "rate limited"),
            OTHER_MPN: _record(OTHER_MPN, manufacturer="Murata"),
        },
    )
    _part(client, db, MPN, linked_provider="mouser")
    _part(client, db, OTHER_MPN, linked_provider="mouser")
    db.commit()

    with pytest.raises(ProviderQuotaExhausted):
        _sweep(db, tmp_path / "dry.csv")


def test_a_quota_stop_commits_what_it_finished_and_exits_3(
    client: TestClient, db, tmp_path: Path, providers
) -> None:
    """Through the CLI, which is where the exit code lives. The first
    part is refreshed and committed; the second hits the wall."""
    ws_id = _signup(client)
    _set_primary(db, ws_id, "mouser")
    first = _record(MPN, manufacturer="Yageo")
    providers["mouser"] = StubProvider(
        "mouser", {MPN: first, OTHER_MPN: "DigiKey rate limit reached"}
    )
    # `mpn` orders the parts only by id, so pin which one is first by
    # giving the quota answer to whichever id sorts second.
    part_a = _part(client, db, MPN, linked_provider="mouser")
    part_b = _part(client, db, OTHER_MPN, linked_provider="mouser")
    if part_b < part_a:
        providers["mouser"].answers = {
            OTHER_MPN: _record(OTHER_MPN, manufacturer="Murata"),
            MPN: "DigiKey rate limit reached",
        }
        part_a, part_b = part_b, part_a
    db.commit()
    report = tmp_path / "apply.csv"

    exit_code = main(
        [JOB_NAME, "--apply", "--report", str(report)],
        session_factory=lambda: db,
        heartbeat_dir=tmp_path / "heartbeats",
    )

    assert exit_code == 3
    db.expire_all()
    assert db.get(Part, part_a).last_refresh_at is not None, "the finished part was kept"
    rows = _report_rows(report)
    assert rows[-1]["action"] == ACTION_ERROR
    assert _summary_rows(report, "count"), "the summary was still written"


# ---------------------------------------------------------------------------
# Throttle
# ---------------------------------------------------------------------------
def test_the_sweep_sleeps_between_provider_calls(
    client: TestClient, db, tmp_path: Path, providers, slept: list[float]
) -> None:
    ws_id = _signup(client)
    _set_primary(db, ws_id, "mouser")
    providers["mouser"] = StubProvider(
        "mouser",
        {
            MPN: _record(MPN, manufacturer="Yageo"),
            OTHER_MPN: _record(OTHER_MPN, manufacturer="Murata"),
        },
    )
    _part(client, db, MPN, linked_provider="mouser")
    _part(client, db, OTHER_MPN, linked_provider="mouser")
    db.commit()

    _sweep(db, tmp_path / "dry.csv", sleep_ms=750)

    # Between, not after: two calls, one gap. The first lookup pays
    # nothing, so a one-part run never sleeps at all.
    assert slept == [0.75]


def test_sleep_ms_zero_turns_the_throttle_off(
    client: TestClient, db, tmp_path: Path, providers, slept: list[float]
) -> None:
    ws_id = _signup(client)
    _set_primary(db, ws_id, "mouser")
    providers["mouser"] = StubProvider(
        "mouser",
        {
            MPN: _record(MPN, manufacturer="Yageo"),
            OTHER_MPN: _record(OTHER_MPN, manufacturer="Murata"),
        },
    )
    _part(client, db, MPN, linked_provider="mouser")
    _part(client, db, OTHER_MPN, linked_provider="mouser")
    db.commit()

    _sweep(db, tmp_path / "dry.csv", sleep_ms=0)

    assert slept == []


# ---------------------------------------------------------------------------
# The report and the audit row
# ---------------------------------------------------------------------------
def test_the_report_carries_per_provider_counts_and_unmapped_keys(
    client: TestClient, db, tmp_path: Path, providers
) -> None:
    """The unmapped list is what extends the alias table in
    `spec_schema_tables.py` — same list, same ranking and same 30-row cap
    as `spec-normalize`, so the two reports mean the same thing."""
    ws_id = _signup(client)
    _set_primary(db, ws_id, "mouser")
    record = _record(MPN, manufacturer="Yageo")
    record["specs"] = record["specs"] + [
        {"key": "Wibble Factor", "value": "high"},
    ]
    providers["mouser"] = StubProvider("mouser", {MPN: record})
    _part(client, db, MPN, linked_provider="mouser")
    db.commit()
    report = tmp_path / "dry.csv"

    _sweep(db, report)

    counts = _summary_rows(report, "count")
    assert ["count", str(ws_id), "mouser", ACTION_REFRESHED, "1"] in counts
    unmapped = [row[2] for row in _summary_rows(report, "unmapped")]
    assert "Wibble Factor" in unmapped


def test_apply_writes_one_sweep_audit_row_per_workspace(
    primary_workspace, db, tmp_path: Path, providers
) -> None:
    """In addition to the per-part `part.specs_reconciled` rows the
    refresh itself writes. Counts only — never a value."""
    ws_id, _ = primary_workspace

    _sweep(db, tmp_path / "apply.csv", apply=True)

    rows = list(
        db.execute(
            select(AuditLog)
            .where(AuditLog.workspace_id == ws_id)
            .where(AuditLog.action == AUDIT_ACTION)
        ).scalars()
    )
    assert len(rows) == 1
    assert f"{ACTION_REFRESHED}=1" in rows[0].comment
    assert "10 kΩ" not in (rows[0].comment or "")


def test_a_dry_run_writes_no_audit_row(
    primary_workspace, db, tmp_path: Path, providers
) -> None:
    ws_id, _ = primary_workspace

    _sweep(db, tmp_path / "dry.csv")

    assert (
        db.execute(
            select(AuditLog)
            .where(AuditLog.workspace_id == ws_id)
            .where(AuditLog.action == AUDIT_ACTION)
        ).scalars().first()
        is None
    )


# ---------------------------------------------------------------------------
# What the sweep shares with the route
# ---------------------------------------------------------------------------
def test_upsert_link_reports_whether_it_inserted(
    client: TestClient, db, providers
) -> None:
    """The sweep's `linked` vs `refreshed` action turns on this, and the
    row looks identical afterwards either way. Reported by the writer, so
    a sweep does not pay a second SELECT per (part, provider) pair to
    re-ask a question it just answered."""
    from app.domain.parts.provider_links import upsert_link

    ws_id = _signup(client)
    part_id = _part(client, db, MPN)

    _, created_first = upsert_link(
        db, workspace_id=ws_id, part_id=part_id, user_id=None, provider="mouser"
    )
    _, created_again = upsert_link(
        db, workspace_id=ws_id, part_id=part_id, user_id=None, provider="mouser"
    )

    assert (created_first, created_again) == (True, False)


def test_the_route_answers_400_for_a_missing_mpn_without_reading_the_tree(
    client: TestClient, db, providers, monkeypatch
) -> None:
    """The category snapshot is a full `part_categories` read. Paying for
    it only to answer 400 is work nobody asked for, so the precondition
    runs first — and it is the service's own, not a second copy."""
    monkeypatch.setattr(
        "app.api.routes.parts_refresh.category_index",
        lambda db, ws_id: pytest.fail("the tree was read before the precondition"),
    )
    ws_id = _signup(client)
    _set_primary(db, ws_id, "mouser")
    part_id = _part(client, db, MPN)
    db.get(Part, part_id).mpn = "   "
    db.commit()

    r = client.post(f"/api/parts/{part_id}/refresh-from-provider")

    assert r.status_code == 400, r.text
    assert "no MPN" in r.json()["status"]["message"]


# ---------------------------------------------------------------------------
# The advisory lock
# ---------------------------------------------------------------------------
@contextmanager
def _lock_held_elsewhere(engine):
    """Hold the sweep's advisory lock on a SEPARATE connection.

    It has to be a different session: Postgres session-level advisory
    locks are re-entrant, so a second `pg_try_advisory_lock` from the
    connection that already holds one succeeds and merely bumps a
    counter. The contention this guards against is between two `run_job`
    processes, which is what a second connection models. Same pattern as
    `test_spec_normalize.py`.
    """
    holder = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    try:
        holder.execute(
            text(
                "SELECT pg_advisory_lock("
                "CAST(:classid AS int4), CAST(hashtext(:key) AS int4))"
            ),
            {"classid": PROVIDER_REFRESH_LOCK_CLASSID, "key": JOB_NAME},
        )
        yield
    finally:
        holder.execute(text("SELECT pg_advisory_unlock_all()"))
        holder.close()


def test_the_two_modules_agree_on_the_lock_key() -> None:
    """`provider_refresh_scope` spells `JOB_NAME` itself, because the job
    module imports it and the arrow may only point one way. A rename on
    one side would silently give the sweep a lock nothing else takes."""
    from app.domain.parts.services import provider_refresh_scope

    assert provider_refresh_scope.JOB_NAME == JOB_NAME


def test_a_second_sweep_is_refused_rather_than_reporting_an_empty_run(
    primary_workspace, db, engine, tmp_path: Path, providers
) -> None:
    """Returning an empty outcome and exit 0 would read as "nothing to
    do", which for a job whose purpose is to change a few hundred parts
    is the most misleading answer available."""
    with _lock_held_elsewhere(engine):
        with pytest.raises(SweepAlreadyRunning):
            _sweep(db, tmp_path / "second.csv")

    assert providers["mouser"].calls == [], "no quota was spent on a refused run"


def test_a_refused_sweep_exits_2_and_leaves_the_report_file_alone(
    primary_workspace, db, engine, tmp_path: Path, providers, capsys
) -> None:
    """The lock is taken BEFORE the report is opened, because opening it
    truncates — a run that may not start must not destroy the CSV of the
    one that is still going."""
    report = tmp_path / "held.csv"
    report.write_text("the running sweep's report\n", encoding="utf-8")

    with _lock_held_elsewhere(engine):
        exit_code = main(
            [JOB_NAME, "--apply", "--report", str(report)],
            session_factory=lambda: db,
            heartbeat_dir=tmp_path / "heartbeats",
        )

    assert exit_code == 2
    assert "already running" in capsys.readouterr().err
    assert report.read_text(encoding="utf-8") == "the running sweep's report\n"


# ---------------------------------------------------------------------------
# Registry + flags
# ---------------------------------------------------------------------------
def test_the_job_is_registered_as_operator_run_and_needs_a_report() -> None:
    spec = JOBS[JOB_NAME]
    assert spec.takes_options is True
    assert spec.interval_setting is None
    assert spec.requires_report is True, "an apply rewrites part columns in place"


def test_apply_without_a_report_is_refused(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main([JOB_NAME, "--apply"], session_factory=_unreachable_session)

    assert exit_code == 2
    assert "requires --report with --apply" in capsys.readouterr().err


@pytest.mark.parametrize(
    "flag",
    [
        "--limit",
        "--only-uncategorized",
        "--link-missing-providers",
        "--include-unlinked",
        "--sleep-ms",
    ],
)
def test_the_job_flags_are_refused_for_other_jobs(
    flag: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """The parser is shared, so argparse accepts every flag for every job.
    Refusing by name is the only way an operator learns the job they
    named does not read it."""
    argv = ["spec-normalize", flag] + (["5"] if flag in ("--limit", "--sleep-ms") else [])

    exit_code = main(argv, session_factory=_unreachable_session)

    assert exit_code == 2
    assert f"takes no {flag}" in capsys.readouterr().err


def test_include_unlinked_is_refused_for_category_seed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A job that creates categories has no notion of a provider link, so
    the flag cannot mean anything to it. Refusing by name is the contract
    every job-specific flag shares — silently ignoring it is the failure
    mode the whole surface exists to prevent."""
    exit_code = main(
        ["category-seed", "--include-unlinked"], session_factory=_unreachable_session
    )

    assert exit_code == 2
    assert "takes no --include-unlinked" in capsys.readouterr().err


def test_sleep_ms_zero_is_still_refused_elsewhere(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`0` is a real value for this flag — the operator turning the
    throttle off — so "not given" cannot be spelled as "falsy"."""
    exit_code = main(
        ["spec-normalize", "--sleep-ms", "0"], session_factory=_unreachable_session
    )

    assert exit_code == 2
    assert "takes no --sleep-ms" in capsys.readouterr().err


@pytest.mark.parametrize("flag", ["--limit", "--sleep-ms"])
def test_a_negative_value_is_a_usage_error(
    flag: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """A negative limit reaches Postgres as `LIMIT -1`, and a negative
    pause would clamp to zero without saying so. Both are typos."""
    exit_code = main([JOB_NAME, flag, "-1"], session_factory=_unreachable_session)

    assert exit_code == 2
    assert f"{flag} must not be negative" in capsys.readouterr().err


def test_the_quota_error_carries_what_the_run_achieved(
    client: TestClient, db, tmp_path: Path, providers
) -> None:
    """Raising is how a halt is reported, so the counts have to travel
    with it — otherwise the only way to read them is to parse the CSV.

    `parts` counts what was VISITED, not what was queued: the batch the
    quota interrupted is not a batch that happened."""
    ws_id = _signup(client)
    _set_primary(db, ws_id, "mouser")
    providers["mouser"] = StubProvider(
        "mouser", {MPN: "rate limit reached", OTHER_MPN: "rate limit reached"}
    )
    _part(client, db, MPN, linked_provider="mouser")
    _part(client, db, OTHER_MPN, linked_provider="mouser")
    db.commit()

    with pytest.raises(ProviderQuotaExhausted) as exc_info:
        _sweep(db, tmp_path / "dry.csv")

    outcome = exc_info.value.outcome
    assert exc_info.value.provider == "mouser"
    assert outcome is not None
    assert outcome.halted_on == "mouser"
    assert outcome.counts[ACTION_ERROR] == 1
    assert outcome.parts == 1, "the second part of the batch was never reached"


def test_the_flags_reach_the_job_that_declares_them() -> None:
    from app.cli.run_job import _options_for, _parse_args

    options = _options_for(
        JOB_NAME,
        _parse_args(
            [
                JOB_NAME,
                "--limit",
                "7",
                "--only-uncategorized",
                "--link-missing-providers",
                "--include-unlinked",
                "--sleep-ms",
                "10",
            ]
        ),
        JOBS,
    )

    assert options is not None
    assert (options.limit, options.sleep_ms) == (7, 10)
    assert options.only_uncategorized is True
    assert options.link_missing_providers is True
    assert options.include_unlinked is True


def test_a_bare_run_through_the_registry_is_a_dry_run(
    primary_workspace, db, tmp_path: Path, providers
) -> None:
    """ADR-0021: every maintenance job is reachable as `run_job <name>`."""
    _, part_id = primary_workspace

    run_job(
        JOB_NAME,
        session_factory=lambda: db,
        heartbeat_dir=tmp_path / "heartbeats",
        options=JobOptions(report=tmp_path / "r.csv"),
    )

    db.expire_all()
    assert db.get(Part, part_id).manufacturer != "Yageo"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _sweep(db, path: Path, **kwargs):
    """`refresh_linked_parts` with the report opened the way the CLI opens
    it — `run_job_options.report_stream` owns the handle in production."""
    with path.open("w", encoding="utf-8", newline="") as stream:
        return refresh_linked_parts(db, stream=stream, **kwargs)


def _unreachable_session():
    raise AssertionError("the flag check must run before any session is opened")
