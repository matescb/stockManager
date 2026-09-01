from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api._helpers import _polymorphic_resolvers, assert_polymorphic_in_workspace
from app.api.routes import stock as stock_routes
from app.core.errors import ErrorCodes
from app.domain.builds.models import Build
from app.domain.lots.models import Lot
from app.domain.orders.models import Order
from app.domain.parts.models import Part, PartCadKey, PartMetaMember, PartSubstitute
from app.domain.projects.bom_import import ParsedRow, _match_part
from app.domain.projects.models import Project
from app.domain.sourcing.budget import BUDGET
from app.domain.sourcing.models import SourcingCache
from app.domain.stock.models import StockEntry
from app.domain.storage.models import StorageLocation
from app.main import app
from tests._factories import (
    add_stock as _factory_add_stock,
)
from tests._factories import (
    create_part as _create_part,
)
from tests._factories import (
    create_project_with_bom as _create_project_with_bom,
)
from tests._factories import (
    create_storage as _create_storage,
)
from tests._factories import (
    signup_user,
)
from tests.test_eda import STEP_BYTES as _EDA_STEP_BYTES
from tests.test_eda import _footprint_text as _eda_footprint_text
from tests.test_eda import _symbol_text as _eda_symbol_text
from tests.test_purchase_plan_route import (
    _configure_sourcing as _configure_purchase_plan_sourcing,
)
from tests.test_purchase_plan_route import (
    _FakeTrustedPartsClient,
    _single_line_project,
)
from tests.test_purchase_plan_route import (
    _offer as _purchase_plan_offer,
)
from tests.test_purchase_plan_route import (
    _post_plan as _post_purchase_plan,
)


def _signup(c: TestClient, email: str) -> str:
    return signup_user(c, email=email).json()["data"]["workspace_id"]


def test_workspace_isolation():
    a = TestClient(app)
    b = TestClient(app)
    ws_a = _signup(a, f"a-{uuid.uuid4().hex[:6]}@x.com")
    ws_b = _signup(b, f"b-{uuid.uuid4().hex[:6]}@x.com")
    assert ws_a != ws_b

    # User A creates a part
    r = a.post("/api/parts", json={"name": "Secret Cap", "part_type": "local"})
    assert r.status_code in (200, 201)
    part_id = r.json()["data"]["id"]

    # User B should not see it
    r = b.get("/api/parts")
    assert r.status_code == 200
    assert all(p["id"] != part_id for p in r.json()["data"])

    # User B trying to GET it directly → 404
    r = b.get(f"/api/parts/{part_id}")
    assert r.status_code == 404


def test_attachments_reject_cross_workspace_object_id():
    """Polymorphic write check on /attachments. Without it, a caller in
    workspace B can attach a file keyed to a part_id owned by workspace A —
    the FK enforces existence, not access."""
    a = TestClient(app)
    b = TestClient(app)
    _signup(a, f"a-{uuid.uuid4().hex[:6]}@x.com")
    _signup(b, f"b-{uuid.uuid4().hex[:6]}@x.com")

    # A creates a part
    r = a.post("/api/parts", json={"name": "A's part", "part_type": "local"})
    assert r.status_code in (200, 201)
    part_a = r.json()["data"]["id"]

    # B tries to attach a file to A's part_id — must 404
    files = {"file": ("s.txt", b"secret", "text/plain")}
    data = {"object_type": "part", "object_id": part_a, "file_type": "other"}
    r = b.post("/api/attachments", data=data, files=files)
    assert r.status_code == 404, r.text

    # And A's by-object listing for that part is still empty (no leak the other way).
    r = a.get(f"/api/attachments/by-object/part/{part_a}")
    assert r.status_code == 200
    assert r.json()["data"] == []


def test_project_entry_rejects_cross_workspace_part_id():
    """Adding/patching a BOM entry must not accept a part_id from another
    workspace. Without this, A could embed B's part UUID in their BOM and
    leak it through downstream joins (build consume, BOM-shortage report)."""
    a = TestClient(app)
    b = TestClient(app)
    _signup(a, f"a-{uuid.uuid4().hex[:6]}@x.com")
    _signup(b, f"b-{uuid.uuid4().hex[:6]}@x.com")

    # B owns a secret part
    r = b.post("/api/parts", json={"name": "B-Secret", "part_type": "local"})
    assert r.status_code in (200, 201)
    secret = r.json()["data"]["id"]

    # A creates a project
    r = a.post("/api/projects", json={"name": "P"})
    assert r.status_code in (200, 201)
    proj = r.json()["data"]["id"]

    # A tries to attach B's part directly — must 404
    r = a.post(
        f"/api/projects/{proj}/entries",
        json={"entry_type": "part", "part_id": secret, "quantity": 1},
    )
    assert r.status_code == 404, r.text

    # A also can't smuggle it in via meta_part_id
    r = a.post(
        f"/api/projects/{proj}/entries",
        json={"entry_type": "meta_part", "meta_part_id": secret, "quantity": 1},
    )
    assert r.status_code == 404, r.text

    # A creates a legitimate unmatched entry, then tries to patch in B's part — must 404
    r = a.post(
        f"/api/projects/{proj}/entries",
        json={"entry_type": "unmatched", "name": "x", "quantity": 1},
    )
    assert r.status_code in (200, 201)
    entry = r.json()["data"]["id"]

    r = a.patch(f"/api/projects/{proj}/entries/{entry}", json={"part_id": secret})
    assert r.status_code == 404, r.text

    r = a.patch(f"/api/projects/{proj}/entries/{entry}", json={"meta_part_id": secret})
    assert r.status_code == 404, r.text

    # And the existing /match endpoint still rejects the same vector (regression
    # pin — it was already correct, this asserts it stays that way).
    r = a.post(f"/api/projects/{proj}/entries/{entry}/match", json={"part_id": secret})
    assert r.status_code == 404, r.text


def test_part_cad_keys_ws(db):
    """CAD-key matching must use the caller workspace, not just cad_key."""
    a = TestClient(app)
    b = TestClient(app)
    ws_a = uuid.UUID(_signup(a, f"a-{uuid.uuid4().hex[:6]}@x.com"))
    ws_b = uuid.UUID(_signup(b, f"b-{uuid.uuid4().hex[:6]}@x.com"))

    part_a = uuid.UUID(_create_part(a, "A-cad-key-part"))
    part_b = uuid.UUID(_create_part(b, "B-cad-key-part"))
    db.add_all(
        [
            PartCadKey(workspace_id=ws_a, part_id=part_a, cad_key="SHARED-CAD-KEY"),
            PartCadKey(workspace_id=ws_b, part_id=part_b, cad_key="SHARED-CAD-KEY"),
            PartCadKey(workspace_id=ws_a, part_id=part_a, cad_key="A-ONLY-CAD-KEY"),
        ]
    )
    db.flush()

    matched_b = _match_part(
        db,
        workspace_id=ws_b,
        row=ParsedRow(cad_key="SHARED-CAD-KEY"),
    )

    assert matched_b is not None
    assert matched_b.id == part_b
    assert _match_part(
        db,
        workspace_id=ws_b,
        row=ParsedRow(cad_key="A-ONLY-CAD-KEY"),
    ) is None
    assert _match_part(
        db,
        workspace_id=ws_a,
        row=ParsedRow(cad_key="A-ONLY-CAD-KEY"),
    ).id == part_a


def test_part_cad_keys_workspace_trigger_rejects_mismatch():
    """migration 0055: direct SQL cannot desync part_cad_keys.workspace_id."""
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError

    from app.infra.db import SessionLocal

    a = TestClient(app)
    b = TestClient(app)
    ws_b = _signup(b, f"b-{uuid.uuid4().hex[:6]}@x.com")
    _signup(a, f"a-{uuid.uuid4().hex[:6]}@x.com")
    part_a = _create_part(a, "A-cad-trigger-part")

    with SessionLocal() as s:
        with pytest.raises((IntegrityError, ProgrammingError, DBAPIError)):
            s.execute(
                text(
                    "INSERT INTO part_cad_keys ("
                    "id, workspace_id, part_id, cad_key, source"
                    ") VALUES ("
                    ":id, :workspace_id, :part_id, 'CAD-X', 'manual'"
                    ")"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "workspace_id": ws_b,
                    "part_id": part_a,
                },
            )
            s.commit()


class _StockNonnegDiag:
    constraint_name = "stock_nonneg_trigger"


class _StockNonnegOrig(Exception):
    diag = _StockNonnegDiag()


def _stock_nonneg_integrity_error() -> IntegrityError:
    return IntegrityError("INSERT INTO stock_entries ...", {}, _StockNonnegOrig())


def test_cross_workspace_error_code_distinct_from_stock_negative(monkeypatch):
    a = TestClient(app)
    b = TestClient(app)
    _signup(a, f"a-{uuid.uuid4().hex[:6]}@x.com")
    _signup(b, f"b-{uuid.uuid4().hex[:6]}@x.com")
    part_a = uuid.UUID(_create_part(a, "A-stock-trigger-part"))

    def insert_foreign_part(db, *, workspace_id, user_id, payload):
        entry = StockEntry(
            workspace_id=workspace_id,
            part_id=part_a,
            quantity_delta=payload.quantity,
            operation_type="add",
            created_by=user_id,
        )
        db.add(entry)
        db.flush()
        return entry

    monkeypatch.setattr(stock_routes, "add_stock", insert_foreign_part)
    cross_workspace = b.post(
        "/api/stock/add",
        json={"part_id": str(uuid.uuid4()), "quantity": 1},
    )

    assert cross_workspace.status_code == 409, cross_workspace.text
    cross_workspace_body = cross_workspace.json()
    assert cross_workspace_body["code"] == ErrorCodes.WORKSPACE_ISOLATION
    assert cross_workspace_body["sqlstate"] == "WS001"

    def raise_stock_negative(*args, **kwargs):
        raise _stock_nonneg_integrity_error()

    monkeypatch.setattr(stock_routes, "remove_stock", raise_stock_negative)
    stock_negative = b.post(
        "/api/stock/remove",
        json={"part_id": str(uuid.uuid4()), "quantity": 1},
    )

    assert stock_negative.status_code == 409, stock_negative.text
    stock_negative_body = stock_negative.json()
    assert stock_negative_body.get("code") != ErrorCodes.WORKSPACE_ISOLATION
    assert stock_negative_body["constraint"] == "stock_nonneg_trigger"


# ---------------------------------------------------------------------------
# Helpers for the cross-tenant FK validation tests below.
# ---------------------------------------------------------------------------


def _two_workspaces():
    a = TestClient(app)
    b = TestClient(app)
    _signup(a, f"a-{uuid.uuid4().hex[:6]}@x.com")
    _signup(b, f"b-{uuid.uuid4().hex[:6]}@x.com")
    return a, b


def _add_stock(
    c: TestClient,
    *,
    part_id: str,
    storage_id: str | None = None,
    quantity: int = 5,
    lot_name: str = "L",
) -> dict:
    return _factory_add_stock(
        c,
        part_id,
        quantity,
        storage_id=storage_id,
        lot_name=lot_name,
    ).json()["data"]


def _assert_not_found_envelope(r) -> None:
    assert r.status_code == 404, r.text
    body = r.json()
    assert body["data"] is None
    assert body["status"]["category"] == "not_found"


def _install_fake_sourcing_provider(monkeypatch, *, mpn: str) -> None:
    _FakeTrustedPartsClient.calls = []
    _FakeTrustedPartsClient.offers_by_mpn = {
        mpn: [_purchase_plan_offer(mpn, distributor="DigiKey", stock=50)]
    }
    BUDGET._events.clear()
    monkeypatch.setattr(
        "app.domain.sourcing.service.make_sourcing_provider",
        lambda workspace: _FakeTrustedPartsClient(workspace.id),
    )


def _create_purchase_plan(client: TestClient, monkeypatch, *, mpn: str) -> str:
    _install_fake_sourcing_provider(monkeypatch, mpn=mpn)
    _configure_purchase_plan_sourcing(client, preferred=["DigiKey"])
    project_id = _single_line_project(client, mpn=mpn, quantity=5)
    r = _post_purchase_plan(client, project_id)
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _create_refreshed_purchase_plan(client: TestClient, monkeypatch, *, mpn: str) -> str:
    plan_id = _create_purchase_plan(client, monkeypatch, mpn=mpn)
    r = client.post(f"/api/sourcing/purchase-plans/{plan_id}/refresh")
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _create_sourcing_alert(client: TestClient) -> str:
    part_id = _create_part(client, "A-alert-target")
    r = client.post(
        "/api/sourcing/alerts",
        json={
            "alert_type": "stock_below",
            "part_id": part_id,
            "threshold": {"qty": 1},
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


# ---------------------------------------------------------------------------
# polymorphic parent resolution registry
# ---------------------------------------------------------------------------


def test_polymorphic_resolvers_complete(db):
    c = TestClient(app)
    workspace_id = uuid.UUID(_signup(c, f"a-{uuid.uuid4().hex[:6]}@x.com"))

    expected_types = {
        "build": Build,
        "lot": Lot,
        "order": Order,
        "part": Part,
        "project": Project,
        "storage_location": StorageLocation,
    }
    assert _polymorphic_resolvers() == expected_types

    part = Part(workspace_id=workspace_id, name="Resolver Part", part_type="local")
    project = Project(workspace_id=workspace_id, name="Resolver Project")
    db.add_all([part, project])
    db.flush()

    parents = {
        "build": Build(
            workspace_id=workspace_id,
            project_id=project.id,
            name="Resolver Build",
        ),
        "lot": Lot(workspace_id=workspace_id, part_id=part.id, source_type="manual"),
        "order": Order(workspace_id=workspace_id, name="Resolver Order"),
        "part": part,
        "project": project,
        "storage_location": StorageLocation(
            workspace_id=workspace_id,
            name="Resolver Storage",
        ),
    }
    db.add_all(parents.values())
    db.flush()

    for object_type, parent in parents.items():
        resolved = assert_polymorphic_in_workspace(
            db,
            object_type,
            parent.id,
            workspace_id,
        )
        assert type(resolved) is expected_types[object_type]
        assert resolved.id == parent.id


# ---------------------------------------------------------------------------
# custom_fields: cross-workspace polymorphic write + source-forge attempt
# ---------------------------------------------------------------------------


def test_custom_fields_reject_cross_workspace_object_id():
    """POST /api/custom-fields validates (object_type, object_id) against
    the caller's workspace. Without this, B can attach attributes to A's
    part — own-ws row + foreign object_id pollutes the cross-tenant graph."""
    a, b = _two_workspaces()
    part_a = _create_part(a, "A-secret")

    r = b.post(
        "/api/custom-fields",
        json={"object_type": "part", "object_id": part_a, "key": "k", "value": "v"},
    )
    assert r.status_code == 404, r.text


def test_custom_fields_unknown_object_type_returns_400():
    """The polymorphic registry is the cross-tenant guardrail. Unknown
    object_types must reject with 400 — not silently store an unscoped row."""
    a, _b = _two_workspaces()
    part_a = _create_part(a)
    r = a.post(
        "/api/custom-fields",
        json={"object_type": "not_a_type", "object_id": part_a, "key": "k", "value": "v"},
    )
    assert r.status_code == 400, r.text


def test_custom_fields_source_cannot_be_forged():
    """A caller posting `source: "provider"` would forge a provider-origin
    row, defeating the override/restore UX. The field must be rejected by
    Pydantic (extra='forbid'), so the response is 422 Unprocessable Entity."""
    a, _b = _two_workspaces()
    part = _create_part(a)
    r = a.post(
        "/api/custom-fields",
        json={
            "object_type": "part",
            "object_id": part,
            "key": "k",
            "value": "v",
            "source": "provider",
        },
    )
    assert r.status_code == 422, r.text


# ---------------------------------------------------------------------------
# tags: cross-workspace polymorphic write
# ---------------------------------------------------------------------------


def test_tags_reject_cross_workspace_object_id_on_link():
    """POST /api/tags/links validates (object_type, object_id) against the
    caller's workspace. Without this, B can label A's part_id."""
    a, b = _two_workspaces()
    part_a = _create_part(a, "A-secret")
    tag_b = b.post("/api/tags", json={"name": "Hot"}).json()["data"]["id"]
    r = b.post(
        "/api/tags/links",
        json={"tag_id": tag_b, "object_type": "part", "object_id": part_a},
    )
    assert r.status_code == 404, r.text


def test_tag_unlink_rejects_foreign_workspace_id():
    """DELETE /api/tags/links/{id} on a foreign-workspace link MUST 404,
    not silently 200. A naive `select … where ws=mine` + `if row: delete`
    pattern returns the same body for "doesn't exist" and "exists in
    another workspace" — that lets B probe A's link ids by their 200 vs.
    404 split (issue #316)."""
    a, b = _two_workspaces()
    part_a = _create_part(a, "A-pn")
    tag_a = a.post("/api/tags", json={"name": "Mine"}).json()["data"]["id"]
    link_a = a.post(
        "/api/tags/links",
        json={"tag_id": tag_a, "object_type": "part", "object_id": part_a},
    ).json()["data"]["id"]

    # B tries to delete A's link; must be 404, not 200.
    r = b.delete(f"/api/tags/links/{link_a}")
    assert r.status_code == 404, r.text

    # And A's link is still there (negative side of the regression).
    listed = a.get(f"/api/tags/by-object/part/{part_a}").json()["data"]
    assert any(t["id"] == link_a and t["tag"]["id"] == tag_a for t in listed)


# ---------------------------------------------------------------------------
# stock service: cross-workspace lot/storage on adjust/remove/move.
# adjust_stock was the active leak — `delta = actual_qty - 0` persists a
# positive entry referencing the foreign FK. remove + move are defense in
# depth (today: current_quantity returns 0, raises insufficient).
# ---------------------------------------------------------------------------


def test_stock_adjust_rejects_foreign_lot_and_storage():
    a, b = _two_workspaces()
    storage_a = _create_storage(a, "A-bin")
    part_a = _create_part(a, "A-part")
    lot_a = _add_stock(a, part_id=part_a, storage_id=storage_a, quantity=5)["lot_id"]

    part_b = _create_part(b, "B-part")

    # B passes A's lot_id — must 422 "lot not found", not silently persist.
    r = b.post(
        "/api/stock/adjust",
        json={
            "part_id": part_b,
            "lot_id": lot_a,
            "actual_quantity": 99,
        },
    )
    assert r.status_code == 400, r.text

    # B passes A's storage_id — same.
    r = b.post(
        "/api/stock/adjust",
        json={
            "part_id": part_b,
            "storage_location_id": storage_a,
            "actual_quantity": 99,
        },
    )
    assert r.status_code == 400, r.text


def test_stock_remove_rejects_foreign_lot_and_storage():
    a, b = _two_workspaces()
    storage_a = _create_storage(a, "A-bin")
    part_a = _create_part(a, "A-part")
    lot_a = _add_stock(a, part_id=part_a, storage_id=storage_a, quantity=5)["lot_id"]

    part_b = _create_part(b, "B-part")
    _add_stock(b, part_id=part_b, quantity=10)

    r = b.post(
        "/api/stock/remove",
        json={"part_id": part_b, "quantity": 1, "lot_id": lot_a},
    )
    assert r.status_code == 400, r.text

    r = b.post(
        "/api/stock/remove",
        json={"part_id": part_b, "quantity": 1, "storage_location_id": storage_a},
    )
    assert r.status_code == 400, r.text


def test_stock_move_rejects_foreign_source():
    a, b = _two_workspaces()
    storage_a = _create_storage(a, "A-bin")
    part_a = _create_part(a, "A-part")
    lot_a = _add_stock(a, part_id=part_a, storage_id=storage_a, quantity=5)["lot_id"]

    part_b = _create_part(b, "B-part")
    storage_b1 = _create_storage(b, "B-bin1")
    storage_b2 = _create_storage(b, "B-bin2")
    _add_stock(b, part_id=part_b, storage_id=storage_b1, quantity=5)

    # B tries to move using A's source_storage_location_id
    r = b.post(
        "/api/stock/move",
        json={
            "part_id": part_b,
            "quantity": 1,
            "source_storage_location_id": storage_a,
            "destination_storage_location_id": storage_b2,
        },
    )
    assert r.status_code == 400, r.text

    # B tries to move using A's source_lot_id
    r = b.post(
        "/api/stock/move",
        json={
            "part_id": part_b,
            "quantity": 1,
            "source_lot_id": lot_a,
            "destination_storage_location_id": storage_b2,
        },
    )
    assert r.status_code == 400, r.text


def test_sourcing_cache_isolated_by_workspace(db):
    """Same query hash in two workspaces must not cross-read cache rows."""
    from app.domain.sourcing.cache import get_or_fetch
    from app.domain.users.models import User
    from app.domain.workspaces.models import Workspace

    def make_workspace():
        user = User(
            email=f"cache-{uuid.uuid4().hex[:8]}@x.com",
            name="cache tester",
            password_hash="test",
        )
        db.add(user)
        db.flush()
        workspace = Workspace(
            name=f"cache-ws-{uuid.uuid4().hex[:8]}",
            kind="organization",
            owner_user_id=user.id,
        )
        db.add(workspace)
        db.flush()
        return workspace.id

    workspace_a = make_workspace()
    workspace_b = make_workspace()
    query = {"mpn": "same-hash", "country": "CZ"}

    get_or_fetch(
        db,
        workspace_id=workspace_a,
        query=query,
        ttl_seconds=3600,
        fetch_fn=lambda: {"workspace": "a"},
    )
    first_b, hit_b = get_or_fetch(
        db,
        workspace_id=workspace_b,
        query=query,
        ttl_seconds=3600,
        fetch_fn=lambda: {"workspace": "b"},
    )

    def fail_workspace_b_miss():
        pytest.fail("workspace B must not miss after writing its own cache row")

    second_b, second_hit_b = get_or_fetch(
        db,
        workspace_id=workspace_b,
        query=query,
        ttl_seconds=3600,
        fetch_fn=fail_workspace_b_miss,
    )

    assert first_b == {"workspace": "b"}
    assert second_b == {"workspace": "b"}
    assert hit_b is False
    assert second_hit_b is True


def test_sourcing_search_uses_caller_workspace_secrets(monkeypatch):
    """The search route must decrypt only the current workspace's sourcing creds."""
    from app.core.secrets import decrypt
    from app.domain.sourcing.budget import BUDGET
    from app.domain.sourcing.schemas import SourcingQuery, SourcingSearchRaw
    from app.domain.workspaces.models import Workspace
    from app.infra.db import SessionLocal

    class RecordingTrustedPartsClient:
        calls: list[dict] = []

        def __init__(
            self,
            company_id: str,
            api_key: str,
            country_code: str | None,
            currency_code: str | None,
            user_agent: str,
            language_code: str | None = None,
        ) -> None:
            self.company_id = company_id
            self.api_key = api_key
            self.country_code = country_code
            self.currency_code = currency_code
            self.language_code = language_code
            self.user_agent = user_agent

        def search(self, queries: list[SourcingQuery], *, use_cached_data: bool, **_kwargs):
            self.calls.append(
                {
                    "company_id": self.company_id,
                    "api_key": self.api_key,
                    "queries": [query.search_token for query in queries],
                    "use_cached_data": use_cached_data,
                }
            )
            return SourcingSearchRaw(offers=[], request_id="workspace-secret-check")

    def configure_sourcing(client: TestClient, company_id: str, api_key: str) -> None:
        r = client.patch(
            "/api/workspaces/current",
            json={
                "sourcing_provider": "trustedparts",
                "sourcing_company_id": company_id,
                "sourcing_api_key": api_key,
                "sourcing_country_code": "CZ",
                "sourcing_currency_code": "EUR",
            },
        )
        assert r.status_code == 200, r.text

    client_a = TestClient(app)
    client_b = TestClient(app)
    ws_a_id = _signup(client_a, f"sourcing-a-{uuid.uuid4().hex[:6]}@x.com")
    ws_b_id = _signup(client_b, f"sourcing-b-{uuid.uuid4().hex[:6]}@x.com")
    configure_sourcing(client_a, "company-a", "api-key-a")
    configure_sourcing(client_b, "company-b", "api-key-b")

    with SessionLocal() as session:
        ws_a = session.get(Workspace, ws_a_id)
        ws_b = session.get(Workspace, ws_b_id)
        assert ws_a is not None
        assert ws_b is not None
        a_tokens = {ws_a.sourcing_api_key_enc}
        b_tokens = {ws_b.sourcing_api_key_enc}

    seen_tokens: list[str | None] = []

    def decrypt_spy(token: str | None) -> str | None:
        seen_tokens.append(token)
        return decrypt(token)

    BUDGET._events.clear()
    RecordingTrustedPartsClient.calls = []
    monkeypatch.setattr("app.domain.sourcing.factory.decrypt", decrypt_spy)
    monkeypatch.setattr(
        "app.domain.sourcing.factory.TrustedPartsClient",
        RecordingTrustedPartsClient,
    )

    r_a = client_a.post("/api/sourcing/search", json={"mpns": ["BAT54C"]})
    assert r_a.status_code == 200, r_a.text
    assert r_a.json()["data"]["cache_hit"] is False
    seen_tokens.clear()

    r = client_b.post("/api/sourcing/search", json={"mpns": ["BAT54C"]})

    assert r.status_code == 200, r.text
    assert r.json()["data"]["cache_hit"] is False
    assert set(seen_tokens) == b_tokens
    assert not set(seen_tokens) & a_tokens
    assert [call["company_id"] for call in RecordingTrustedPartsClient.calls] == ["", ""]
    assert RecordingTrustedPartsClient.calls[-1]["api_key"] == "api-key-b"


def test_purchase_plan_isolated_by_workspace(monkeypatch):
    a, b = _two_workspaces()
    part_a = _create_part(a, "A-plan-part")
    project_a = _create_project_with_bom(
        a,
        "A plan",
        [{"part_id": part_a, "quantity": 1}],
    )

    def fail_build_purchase_plan(*_args, **_kwargs):
        pytest.fail("foreign project id reached purchase-plan service")

    monkeypatch.setattr(
        "app.domain.sourcing.service.build_purchase_plan",
        fail_build_purchase_plan,
    )

    r = b.post(
        f"/api/projects/{project_a}/purchase-plan",
        json={"build_quantity": 1},
    )

    assert r.status_code == 404, r.text
    assert r.json()["status"]["category"] == "not_found"


def test_purchase_plan_refresh_returns_404_for_foreign_workspace(monkeypatch):
    a, b = _two_workspaces()
    plan_a = _create_purchase_plan(a, monkeypatch, mpn="FOREIGN-REFRESH")

    r = b.post(f"/api/sourcing/purchase-plans/{plan_a}/refresh")

    _assert_not_found_envelope(r)


def test_purchase_plan_get_returns_404_for_foreign_workspace(monkeypatch):
    a, b = _two_workspaces()
    plan_a = _create_purchase_plan(a, monkeypatch, mpn="FOREIGN-GET")
    project_b = _single_line_project(b, mpn="LOCAL-GET", quantity=1)

    r = b.get(f"/api/projects/{project_b}/purchase-plans/{plan_a}")

    _assert_not_found_envelope(r)


def test_purchase_plan_convert_to_orders_returns_404_for_foreign_workspace(monkeypatch):
    a, b = _two_workspaces()
    plan_a = _create_refreshed_purchase_plan(a, monkeypatch, mpn="FOREIGN-ORDERS")

    r = b.post(f"/api/sourcing/purchase-plans/{plan_a}/orders")

    _assert_not_found_envelope(r)


def test_sourcing_alerts_isolated_by_workspace():
    a, b = _two_workspaces()
    part_a = _create_part(a, "A-alert-part")
    part_b = _create_part(b, "B-alert-part")
    alert_a = a.post(
        "/api/sourcing/alerts",
        json={
            "alert_type": "stock_below",
            "part_id": part_a,
            "threshold": {"qty": 1},
        },
    ).json()["data"]
    alert_b = b.post(
        "/api/sourcing/alerts",
        json={
            "alert_type": "stock_below",
            "part_id": part_b,
            "threshold": {"qty": 1},
        },
    ).json()["data"]

    listed_b = b.get("/api/sourcing/alerts").json()["data"]["items"]

    assert alert_b["id"] in {alert["id"] for alert in listed_b}
    assert alert_a["id"] not in {alert["id"] for alert in listed_b}


def test_alert_get_returns_404_for_foreign_workspace():
    a, b = _two_workspaces()
    alert_a = _create_sourcing_alert(a)

    r = b.get(f"/api/sourcing/alerts/{alert_a}")

    _assert_not_found_envelope(r)


def test_alert_patch_returns_404_for_foreign_workspace():
    a, b = _two_workspaces()
    alert_a = _create_sourcing_alert(a)

    r = b.patch(f"/api/sourcing/alerts/{alert_a}", json={"enabled": False})

    _assert_not_found_envelope(r)


def test_alert_delete_returns_404_for_foreign_workspace():
    a, b = _two_workspaces()
    alert_a = _create_sourcing_alert(a)

    r = b.delete(f"/api/sourcing/alerts/{alert_a}")

    _assert_not_found_envelope(r)


def test_part_sourcing_refresh_returns_404_for_foreign_workspace():
    a, b = _two_workspaces()
    part_a = _create_part(a, "A-sourcing-refresh", mpn="FOREIGN-SOURCING")

    r = b.post(f"/api/parts/{part_a}/sourcing/refresh")

    _assert_not_found_envelope(r)


# ---------------------------------------------------------------------------
# parts.default_storage_location_id on create / patch / bulk-import-from-scan
# ---------------------------------------------------------------------------


def test_parts_create_rejects_foreign_default_storage():
    a, b = _two_workspaces()
    storage_a = _create_storage(a)

    r = b.post(
        "/api/parts",
        json={
            "name": "X",
            "part_type": "local",
            "default_storage_location_id": storage_a,
        },
    )
    assert r.status_code == 404, r.text


def test_parts_patch_rejects_foreign_default_storage():
    a, b = _two_workspaces()
    storage_a = _create_storage(a)
    part_b = _create_part(b)

    r = b.patch(
        f"/api/parts/{part_b}",
        json={"default_storage_location_id": storage_a},
    )
    assert r.status_code == 404, r.text


def test_no_cross_workspace_default_storage_in_existing_rows():
    """Audit-shape pin (DB-015): after a normal multi-workspace test
    run, there must be zero `parts` rows whose
    `default_storage_location_id` references a `storage_locations` row
    in a different workspace. Doubles as the form of the prod audit
    query the issue suggests running once on `main`."""
    from sqlalchemy import text

    from app.infra.db import SessionLocal

    a, b = _two_workspaces()
    storage_a = _create_storage(a, "A-bin")
    _ = _create_part(a, "A-part")
    storage_b = _create_storage(b, "B-bin")
    _ = _create_part(b, "B-part")

    # Set defaults via the API (the legitimate path).
    parts_a = a.get("/api/parts").json()["data"]
    parts_b = b.get("/api/parts").json()["data"]
    a.patch(
        f"/api/parts/{parts_a[0]['id']}",
        json={"default_storage_location_id": storage_a},
    )
    b.patch(
        f"/api/parts/{parts_b[0]['id']}",
        json={"default_storage_location_id": storage_b},
    )

    with SessionLocal() as s:
        bad = s.execute(
            text(
                "SELECT p.id FROM parts p "
                "JOIN storage_locations sl ON p.default_storage_location_id = sl.id "
                "WHERE p.workspace_id <> sl.workspace_id"
            )
        ).fetchall()
    assert bad == [], (
        f"cross-workspace default_storage_location_id rows leaked: {bad}"
    )


def test_raw_update_cannot_smuggle_cross_workspace_default_storage():
    """DB-015 Phase 2 (migration 0036). The BEFORE trigger
    `parts_default_storage_workspace_check` must reject a direct SQL UPDATE
    that points default_storage_location_id at a storage_location in a
    different workspace."""
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError

    from app.infra.db import SessionLocal

    a, b = _two_workspaces()
    storage_a = _create_storage(a, "A-bin")
    part_b = _create_part(b, "B-part")

    # Direct SQL: bypass the route layer entirely.  The trigger must fire
    # and raise an error (ERRCODE 23514 → IntegrityError in SQLAlchemy).
    with SessionLocal() as s:
        with pytest.raises((IntegrityError, ProgrammingError, DBAPIError)):
            s.execute(
                text(
                    "UPDATE parts SET default_storage_location_id = :sid "
                    "WHERE id = :pid"
                ),
                {"sid": storage_a, "pid": part_b},
            )
            s.commit()


def test_raw_update_same_workspace_default_storage_succeeds():
    """Positive path for migration 0036: a raw SQL UPDATE that sets
    default_storage_location_id to a storage_location in the *same*
    workspace must be accepted by the trigger."""
    from sqlalchemy import text

    from app.infra.db import SessionLocal

    a, _ = _two_workspaces()
    storage_a = _create_storage(a, "A-bin")
    part_a = _create_part(a, "A-part")

    with SessionLocal() as s:
        s.execute(
            text(
                "UPDATE parts SET default_storage_location_id = :sid "
                "WHERE id = :pid"
            ),
            {"sid": storage_a, "pid": part_a},
        )
        s.commit()

        result = s.execute(
            text("SELECT default_storage_location_id FROM parts WHERE id = :pid"),
            {"pid": part_a},
        ).scalar()

    assert str(result) == storage_a, (
        "same-workspace UPDATE should persist; trigger incorrectly rejected it"
    )


def test_raw_insert_cross_workspace_default_storage_rejected():
    """DB-015 Phase 2 (migration 0036). The BEFORE trigger must also fire on
    INSERT, preventing a cross-workspace default_storage_location_id from
    ever being written via a direct SQL INSERT."""
    import uuid

    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError

    from app.infra.db import SessionLocal

    a, b = _two_workspaces()
    storage_a = _create_storage(a, "A-bin")

    # We need the workspace_id for workspace B.
    with SessionLocal() as s:
        ws_b_id = s.execute(
            text("SELECT workspace_id FROM parts WHERE workspace_id != :wsa LIMIT 1"),
            {"wsa": storage_a[:36]},  # narrow to any workspace that isn't A
        ).scalar()
        # Fall back: look up via a workspace that owns storage_a
        ws_a_id = s.execute(
            text("SELECT workspace_id FROM storage_locations WHERE id = :sid"),
            {"sid": storage_a},
        ).scalar()

    # Find workspace B's actual id via a separate query
    with SessionLocal() as s:
        ws_b_id = s.execute(
            text(
                "SELECT id FROM workspaces WHERE id != :wsa LIMIT 1"
            ),
            {"wsa": ws_a_id},
        ).scalar()

    new_part_id = str(uuid.uuid4())
    with SessionLocal() as s:
        with pytest.raises((IntegrityError, ProgrammingError, DBAPIError)):
            s.execute(
                text(
                    "INSERT INTO parts (id, workspace_id, name, part_type, "
                    "default_storage_location_id, created_at, updated_at) "
                    "VALUES (:id, :ws, 'injected', 'local', :sid, NOW(), NOW())"
                ),
                {"id": new_part_id, "ws": ws_b_id, "sid": storage_a},
            )
            s.commit()


def test_stock_entries_ws_trigger():
    """AUD-052 / migration 0050. Direct SQL must not be able to attach a
    stock_entries row to workspace-scoped objects from another workspace."""
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError

    from app.infra.db import SessionLocal

    a = TestClient(app)
    b = TestClient(app)
    _signup(a, f"a-{uuid.uuid4().hex[:6]}@x.com")
    ws_b = _signup(b, f"b-{uuid.uuid4().hex[:6]}@x.com")

    part_a = _create_part(a, "A-stock-trigger-part")
    part_b = _create_part(b, "B-stock-trigger-part")
    storage_a = _create_storage(a, "A-stock-trigger-bin")
    stock_a = _add_stock(
        a,
        part_id=part_a,
        storage_id=storage_a,
        quantity=5,
        lot_name="A-stock-trigger-lot",
    )
    project_a = a.post("/api/projects", json={"name": "A-stock-trigger-project"}).json()[
        "data"
    ]["id"]
    build_a = a.post(
        "/api/builds",
        json={"name": "A-stock-trigger-build", "project_id": project_a},
    ).json()["data"]["id"]
    order_a = a.post(
        "/api/orders",
        json={
            "name": "A-stock-trigger-order",
            "entries": [{"part_id": part_a, "quantity_ordered": 1}],
        },
    ).json()["data"]["id"]
    order_entry_a = a.get(f"/api/orders/{order_a}").json()["data"]["entries"][0]["id"]

    insert_sql = text(
        "INSERT INTO stock_entries ("
        "id, workspace_id, part_id, lot_id, storage_location_id, "
        "quantity_delta, status, operation_type, related_entry_id, "
        "order_id, order_entry_id, project_id, build_id, occurred_at, created_at"
        ") VALUES ("
        ":id, :workspace_id, :part_id, :lot_id, :storage_location_id, "
        "1, 'on_hand', 'audit_probe', :related_entry_id, "
        ":order_id, :order_entry_id, :project_id, :build_id, NOW(), NOW()"
        ")"
    )

    def insert_stock_entry(**overrides):
        params = {
            "id": str(uuid.uuid4()),
            "workspace_id": ws_b,
            "part_id": part_b,
            "lot_id": None,
            "storage_location_id": None,
            "related_entry_id": None,
            "order_id": None,
            "order_entry_id": None,
            "project_id": None,
            "build_id": None,
        }
        params.update(overrides)
        with SessionLocal() as s:
            s.execute(insert_sql, params)
            s.commit()
        return params["id"]

    valid_entry_id = insert_stock_entry()

    with SessionLocal() as s:
        with pytest.raises((IntegrityError, ProgrammingError, DBAPIError)):
            s.execute(
                text("UPDATE stock_entries SET part_id = :part_id WHERE id = :id"),
                {"part_id": part_a, "id": valid_entry_id},
            )
            s.commit()

    cross_workspace_refs = [
        {"part_id": part_a},
        {"lot_id": stock_a["lot_id"]},
        {"storage_location_id": storage_a},
        {"related_entry_id": stock_a["id"]},
        {"order_id": order_a},
        {"order_entry_id": order_entry_a},
        {"project_id": project_a},
        {"build_id": build_a},
    ]
    for overrides in cross_workspace_refs:
        with pytest.raises((IntegrityError, ProgrammingError, DBAPIError)):
            insert_stock_entry(**overrides)


def test_part_substitute_cross_workspace_db_trigger(db):
    """AUD-051 / migration 0053. Direct SQL cannot link substitute parts
    across workspaces, even when the route layer is bypassed."""
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError

    from app.infra.db import SessionLocal

    a = TestClient(app)
    b = TestClient(app)
    _signup(a, f"a-{uuid.uuid4().hex[:6]}@x.com")
    ws_b = _signup(b, f"b-{uuid.uuid4().hex[:6]}@x.com")

    part_a = _create_part(a, "A-substitute-trigger-part")
    part_b = _create_part(b, "B-substitute-trigger-part")
    substitute_b = _create_part(b, "B-substitute-trigger-alt")

    row = PartSubstitute(
        workspace_id=uuid.UUID(ws_b),
        part_id=uuid.UUID(part_b),
        substitute_part_id=uuid.UUID(substitute_b),
    )
    db.add(row)
    db.flush()

    with SessionLocal() as s:
        with pytest.raises((IntegrityError, ProgrammingError, DBAPIError)):
            s.execute(
                text(
                    "INSERT INTO part_substitutes "
                    "(id, workspace_id, part_id, substitute_part_id, direction) "
                    "VALUES (:id, :workspace_id, :part_id, :substitute_part_id, "
                    "'bidirectional')"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "workspace_id": ws_b,
                    "part_id": part_b,
                    "substitute_part_id": part_a,
                },
            )
            s.commit()

    with SessionLocal() as s:
        with pytest.raises((IntegrityError, ProgrammingError, DBAPIError)):
            s.execute(
                text(
                    "UPDATE part_substitutes "
                    "SET substitute_part_id = :substitute_part_id "
                    "WHERE id = :id"
                ),
                {"id": row.id, "substitute_part_id": part_a},
            )
            s.commit()


def test_part_meta_member_cross_workspace_db_trigger(db):
    """AUD-051 / migration 0053. Direct SQL cannot attach a meta-part member
    from another workspace."""
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError

    from app.infra.db import SessionLocal

    a = TestClient(app)
    b = TestClient(app)
    _signup(a, f"a-{uuid.uuid4().hex[:6]}@x.com")
    ws_b = _signup(b, f"b-{uuid.uuid4().hex[:6]}@x.com")

    member_a = _create_part(a, "A-meta-trigger-member")
    meta_b = _create_part(b, "B-meta-trigger-meta", part_type="meta")
    member_b = _create_part(b, "B-meta-trigger-member")

    row = PartMetaMember(
        workspace_id=uuid.UUID(ws_b),
        meta_part_id=uuid.UUID(meta_b),
        part_id=uuid.UUID(member_b),
    )
    db.add(row)
    db.flush()

    with SessionLocal() as s:
        with pytest.raises((IntegrityError, ProgrammingError, DBAPIError)):
            s.execute(
                text(
                    "INSERT INTO part_meta_members "
                    "(id, workspace_id, meta_part_id, part_id) "
                    "VALUES (:id, :workspace_id, :meta_part_id, :part_id)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "workspace_id": ws_b,
                    "meta_part_id": meta_b,
                    "part_id": member_a,
                },
            )
            s.commit()

    with SessionLocal() as s:
        with pytest.raises((IntegrityError, ProgrammingError, DBAPIError)):
            s.execute(
                text(
                    "UPDATE part_meta_members SET part_id = :part_id "
                    "WHERE id = :id"
                ),
                {"id": row.id, "part_id": member_a},
            )
            s.commit()


def test_parts_bulk_import_rejects_foreign_storage():
    """bulk-import-from-scan must validate row.storage_location_id against
    the caller's workspace BEFORE creating the part. The whole-batch loop
    keeps running; only the offending row is reported as `invalid`."""
    a, b = _two_workspaces()
    storage_a = _create_storage(a)

    # B has to have a provider configured for bulk-import to even reach the
    # row-validation step (the endpoint short-circuits if no provider). Use
    # the "none" path — set parts_provider to mouser and a fake key just
    # so the configuration check passes; the foreign-storage validation
    # fires before the provider lookup.
    r = b.patch(
        "/api/workspaces/current",
        json={"parts_provider": "mouser", "parts_provider_api_key": "x" * 36},
    )
    assert r.status_code == 200, r.text

    r = b.post(
        "/api/parts/bulk-import-from-scan",
        json={
            "rows": [
                # Row 1: foreign storage — must be marked invalid, not block the loop.
                {
                    "mpn": "RC0402-1K",
                    "storage_location_id": storage_a,
                    "quantity": 1,
                },
                # Row 2: no storage at all — proves the per-row independence;
                # mpn won't resolve (fake key, no real provider) so it ends as
                # lookup_failed, but it MUST run rather than be skipped because
                # row 1 failed.
                {"mpn": "RC0402-2K", "quantity": 1},
            ]
        },
    )
    # Endpoint returns 200 with per-row outcomes.
    assert r.status_code == 200, r.text
    out = r.json()["data"]["rows"]
    assert len(out) == 2
    assert out[0]["status"] == "invalid"
    assert "storage" in (out[0].get("error") or "").lower()
    # Row 2 was processed (per-row independence) and reached the provider,
    # which fails because the API key is fake — but the loop did NOT abort
    # on row 1.
    assert out[1]["status"] == "lookup_failed"


# ---------------------------------------------------------------------------
# builds.consume: cross-workspace lot / storage on a consume line.
# Defense in depth — current_quantity ws-filter would return 0 today,
# blocking the write. Pinned so a future refactor can't reopen the leak.
# ---------------------------------------------------------------------------


def test_builds_consume_rejects_foreign_lot_and_storage():
    a, b = _two_workspaces()
    storage_b = _create_storage(b, "B-bin")
    part_b = _create_part(b, "B-part")
    lot_b = _add_stock(b, part_id=part_b, storage_id=storage_b, quantity=5)["lot_id"]

    # A sets up a project + entry pointing at A's part, then a build.
    storage_a = _create_storage(a, "A-bin")
    part_a = _create_part(a, "A-part")
    _add_stock(a, part_id=part_a, storage_id=storage_a, quantity=10)

    proj_a = a.post("/api/projects", json={"name": "P"}).json()["data"]["id"]
    entry = a.post(
        f"/api/projects/{proj_a}/entries",
        json={"entry_type": "part", "part_id": part_a, "quantity": 1},
    ).json()["data"]["id"]
    build_a = a.post(
        "/api/builds",
        json={"name": "B1", "project_id": proj_a, "quantity": 1},
    ).json()["data"]["id"]

    # A's build, A's entry, A's part — but the line claims B's lot. Must reject.
    r = a.post(
        f"/api/builds/{build_a}/consume",
        json={
            "lines": [
                {
                    "project_entry_id": entry,
                    "part_id": part_a,
                    "lot_id": lot_b,
                    "quantity": 1,
                }
            ]
        },
    )
    assert r.status_code == 400, r.text

    # And likewise for storage_location_id.
    r = a.post(
        f"/api/builds/{build_a}/consume",
        json={
            "lines": [
                {
                    "project_entry_id": entry,
                    "part_id": part_a,
                    "storage_location_id": storage_b,
                    "quantity": 1,
                }
            ]
        },
    )
    assert r.status_code == 400, r.text


# ---------------------------------------------------------------------------
# TEST-001 router coverage extension. Each test below pins workspace
# isolation on a router that the original matrix didn't cover. The
# pattern is the same: create resource X in workspace A, prove that
# workspace B can't read or mutate it via that router's primary
# endpoints (404 on direct fetches; foreign IDs in bodies must reject).
# ---------------------------------------------------------------------------


def test_lots_isolation_get_and_history_404_across_workspaces():
    a, b = _two_workspaces()
    storage_a = _create_storage(a)
    part_a = _create_part(a)
    lot_a = _add_stock(a, part_id=part_a, storage_id=storage_a, quantity=2)["lot_id"]

    # B's GET /api/lots must not see A's lot
    rows = b.get("/api/lots").json()["data"]
    assert all(r["id"] != lot_a for r in rows)

    # Direct GET, PATCH, /move, /adjust-count, /history all 404 for B.
    assert b.get(f"/api/lots/{lot_a}").status_code == 404
    assert b.patch(f"/api/lots/{lot_a}", json={"name": "stolen"}).status_code == 404
    assert b.get(f"/api/lots/{lot_a}/history").status_code == 404


def test_orders_isolation_get_and_archive_404_across_workspaces():
    a, b = _two_workspaces()
    order_a = a.post("/api/orders", json={"name": "OA"}).json()["data"]["id"]
    # B's listing must not include it
    rows = b.get("/api/orders").json()["data"]
    assert all(o["id"] != order_a for o in rows)

    assert b.get(f"/api/orders/{order_a}").status_code == 404
    assert b.patch(f"/api/orders/{order_a}", json={"name": "stolen"}).status_code == 404
    assert b.get(f"/api/orders/{order_a}/activity").status_code == 404


def test_orders_create_rejects_foreign_part_id_via_entries():
    """Create-order with an entries[].part_id from another workspace must
    return 404. Without ws-validation, a B caller could embed A's part UUID
    in an OrderEntry, leaking the existence-oracle and binding the entry to
    a foreign row."""
    a, b = _two_workspaces()
    part_a = _create_part(a, "A-part")
    r = b.post(
        "/api/orders",
        json={
            "name": "B-order",
            "entries": [
                {"part_id": part_a, "name": "smuggled", "quantity_ordered": 1}
            ],
        },
    )
    assert r.status_code == 404


def test_orders_add_entry_rejects_foreign_part_id():
    """POST /api/orders/{id}/entries with a part_id from another workspace
    must return 404 (workspace isolation on add_entry)."""
    a, b = _two_workspaces()
    part_a = _create_part(a, "A-part-entry")
    # B creates its own order
    order_b = b.post("/api/orders", json={"name": "B-order"}).json()["data"]["id"]
    # B tries to add an entry referencing A's part
    r = b.post(
        f"/api/orders/{order_b}/entries",
        json={"part_id": part_a, "name": "smuggled", "quantity_ordered": 1},
    )
    assert r.status_code == 404


def test_invitations_token_redemption_does_not_cross_workspaces():
    """Invitation tokens are scoped to the issuing workspace. Pasting
    an A-issued token into B's accept call must not graft B onto A.
    The bearer-token pattern has been bitten by this in past — pin it."""
    a, _b = _two_workspaces()
    # A invites a fresh email
    invitee = f"x-{uuid.uuid4().hex[:6]}@x.com"
    inv = a.post(
        "/api/invitations",
        json={"email": invitee, "role": "viewer"},
    ).json()["data"]
    token = inv["token"]
    assert token

    # A different existing user (we use B's admin) tries to redeem the
    # token on their own session. The route accepts on signup-by-token,
    # but redemption with a non-matching email must reject — the
    # invitation flow keys on the email, not just the token. If the
    # server didn't bind to email at all, B's admin could escalate
    # into A's workspace just by knowing the token.
    fresh = TestClient(app)
    _signup(fresh, f"unrelated-{uuid.uuid4().hex[:6]}@x.com")
    r = fresh.post("/api/invitations/accept", json={"token": token})
    # The expected behaviour is reject (token bound to invitee_email).
    # If accepted, fresh would now be a member of A — that's the
    # security failure this test catches.
    assert r.status_code in (400, 403, 404), r.text


def test_bom_presets_isolation():
    a, b = _two_workspaces()
    preset = a.post(
        "/api/bom-presets",
        json={"name": "A's", "config": {"sep": ","}},
    ).json()["data"]["id"]
    rows = b.get("/api/bom-presets").json()["data"]
    assert all(r["id"] != preset for r in rows)
    assert b.get(f"/api/bom-presets/{preset}").status_code == 404
    assert b.patch(f"/api/bom-presets/{preset}", json={"name": "stolen"}).status_code == 404
    assert b.delete(f"/api/bom-presets/{preset}").status_code == 404


def test_categories_isolation():
    a, b = _two_workspaces()
    category = a.post("/api/categories", json={"name": "A's"}).json()["data"]["id"]
    rows = b.get("/api/categories").json()["data"]
    assert all(r["id"] != category for r in rows)
    assert b.patch(f"/api/categories/{category}", json={"name": "stolen"}).status_code == 404
    assert b.post(f"/api/categories/{category}/archive").status_code == 404
    assert b.post(f"/api/categories/{category}/restore").status_code == 404
    # And the FK on parts must refuse a foreign category the same way.
    part = b.post("/api/parts", json={"name": "B's part"}).json()["data"]["id"]
    assert b.patch(f"/api/parts/{part}", json={"category_id": category}).status_code == 404


def test_bom_provider_import_foreign_project_404():
    """Provider-backed unmatched-row import must resolve project_id inside
    the caller's workspace before it looks at any BOM entries."""
    a, b = _two_workspaces()
    b.patch(
        "/api/workspaces/current",
        json={"parts_provider": "mouser", "parts_provider_api_key": "x" * 36},
    )
    project_a = a.post("/api/projects", json={"name": "A-provider-import"}).json()["data"]["id"]

    r = b.post(
        f"/api/projects/{project_a}/bom/import-from-provider",
        json={"entry_ids": None},
    )
    assert r.status_code == 404, r.text


def test_reports_low_stock_does_not_leak_other_workspace_parts():
    """Reports run as workspace-scoped aggregations. A foreign threshold-
    flagged part must not appear in B's low-stock report."""
    a, b = _two_workspaces()
    a.post(
        "/api/parts",
        json={"name": "A-flagged", "part_type": "local", "low_stock_report_quantity": 100},
    )
    rows = b.get("/api/reports/low-stock").json()["data"]
    assert all(r["name"] != "A-flagged" for r in rows)


def test_reports_bom_shortage_404s_for_foreign_project():
    a, b = _two_workspaces()
    proj_a = a.post("/api/projects", json={"name": "PA"}).json()["data"]["id"]
    r = b.get(f"/api/reports/bom-shortage?project_id={proj_a}&quantity=1")
    assert r.status_code == 404, r.text


def test_search_does_not_leak_other_workspace_results():
    a, b = _two_workspaces()
    _create_part(a, "RareWidget1234")
    a.post("/api/storage", json={"name": "Hidden-Bin"})
    a.post("/api/projects", json={"name": "Project-Secret"})

    res = b.get("/api/search?q=RareWidget1234").json()["data"]
    assert res["parts"] == []
    res = b.get("/api/search?q=Hidden-Bin").json()["data"]
    assert res["storage_locations"] == []
    res = b.get("/api/search?q=Project-Secret").json()["data"]
    assert res["projects"] == []


def test_parts_provider_lookup_uses_caller_workspace_secrets():
    """The parts_provider router decrypts the CALLER's workspace
    secrets, not the target part's. Cross-tenant configuration leak
    would mean B could trigger A's API key. We prove this by verifying
    that with no provider configured on B, the lookup returns the
    "no provider" envelope — even after A configures one."""
    a, b = _two_workspaces()
    # A configures a fake mouser key
    a.patch(
        "/api/workspaces/current",
        json={"parts_provider": "mouser", "parts_provider_api_key": "x" * 36},
    )
    # B has no provider; lookup must fall through with a non-found
    # response, never use A's key.
    r = b.post("/api/parts/lookup-mpn", json={"mpn": "1N4148"})
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["found"] is False
    # The provider name must reflect B's empty config, not A's "mouser"
    assert body["provider"] in (None, "none", ""), body


def test_sourcing_bom_isolated_by_workspace(db, monkeypatch):
    """BOM sourcing must use the caller workspace for both provider config
    and cache rows. Same MPN in two workspaces should produce two scoped
    sourcing_cache rows, never a cross-tenant cache hit."""
    BUDGET._events.clear()
    calls: list[dict] = []

    def fake_post(_url, json, headers):
        mpn = json["Queries"][0]["SearchToken"]
        company_id = headers["X-Api-Key"].removeprefix("key-")
        distributor = "SourceA" if company_id == "company-a" else "SourceB"
        calls.append({"company_id": company_id, "mpn": mpn})
        assert "CompanyId" not in json
        assert "ApiKey" not in json
        return (
            200,
            {
                "PartResults": [
                    {
                        "PartNumber": mpn,
                        "Manufacturer": "IsolationCo",
                        "ProductUrl": f"https://www.trustedparts.com/{mpn}",
                        "Distributors": [
                            {
                                "Name": distributor,
                                "DistributorResults": [
                                    {
                                        "DistributorPartNumber": f"{mpn}-{distributor}",
                                        "Pricing": {
                                            "CurrencyCode": "EUR",
                                            "MinimumQuantity": 1,
                                            "Prices": [{"Quantity": 1, "Amount": 1.25}],
                                        },
                                        "Stock": {"QuantityOnHand": 10},
                                        "Packaging": [{"MinimumOrderQuantity": 1}],
                                        "Links": [],
                                    }
                                ],
                            }
                        ],
                    }
                ],
            },
        )

    monkeypatch.setattr("app.domain.sourcing.client._post_tp", fake_post)
    a, b = _two_workspaces()
    for client, company_id in ((a, "company-a"), (b, "company-b")):
        r = client.patch(
            "/api/workspaces/current",
            json={
                "sourcing_provider": "trustedparts",
                "sourcing_company_id": company_id,
                "sourcing_api_key": f"key-{company_id}",
                "sourcing_country_code": "CZ",
                "sourcing_currency_code": "EUR",
                "sourcing_use_cached_for_dashboards": False,
            },
        )
        assert r.status_code == 200, r.text

    part_a = _create_part(a, "Shared A", mpn="ISO-SHARED")
    project_a = a.post("/api/projects", json={"name": "A BOM"}).json()["data"]["id"]
    a.post(
        f"/api/projects/{project_a}/entries",
        json={"entry_type": "part", "part_id": part_a, "quantity": 1},
    )
    part_b = _create_part(b, "Shared B", mpn="ISO-SHARED")
    project_b = b.post("/api/projects", json={"name": "B BOM"}).json()["data"]["id"]
    b.post(
        f"/api/projects/{project_b}/entries",
        json={"entry_type": "part", "part_id": part_b, "quantity": 1},
    )
    ws_a = a.get("/api/workspaces/current").json()["data"]["id"]
    ws_b = b.get("/api/workspaces/current").json()["data"]["id"]

    r_a = a.post(f"/api/projects/{project_a}/sourcing", json={"build_quantity": 1})
    r_b = b.post(f"/api/projects/{project_b}/sourcing", json={"build_quantity": 1})

    assert r_a.status_code == 200, r_a.text
    assert r_b.status_code == 200, r_b.text
    offer_a = r_a.json()["data"]["rows"][0]["offers"][0]
    offer_b = r_b.json()["data"]["rows"][0]["offers"][0]
    assert offer_a["distributor"] == "SourceA"
    assert offer_b["distributor"] == "SourceB"
    assert offer_a["distributor"] != offer_b["distributor"]
    assert calls == [
        {"company_id": "company-a", "mpn": "ISO-SHARED"},
        {"company_id": "company-b", "mpn": "ISO-SHARED"},
    ]

    rows = db.execute(
        select(SourcingCache).where(SourcingCache.query_json["mpn"].astext == "ISO-SHARED")
    ).scalars().all()
    assert {str(row.workspace_id) for row in rows} == {ws_a, ws_b}
    assert len(rows) == 2
    assert len({row.query_hash for row in rows}) == 2
    assert {row.query_json["workspace_id"] for row in rows} == {ws_a, ws_b}

    BUDGET._events.clear()


def test_catalog_token_404s_on_wrong_workspace_token():
    """Catalog tokens are workspace-scoped and gate the only public
    surface. A made-up / cross-workspace token must 404 — not leak any
    workspace's metadata."""
    a, _b = _two_workspaces()
    # A enables their catalog
    r = a.patch("/api/workspaces/current", json={"catalog_enabled": True})
    assert r.status_code == 200, r.text
    assert a.get("/api/auth/me").status_code == 200
    # Fetching with a fake token must 404 — and we never authenticated
    # the call to /catalog so this is the unauthenticated probe.
    fresh = TestClient(app)
    bad = "deadbeef" * 8
    assert fresh.get(f"/catalog/{bad}").status_code == 404
    assert fresh.get(f"/catalog/{bad}/parts.json").status_code == 404


def test_part_activity_does_not_leak_cross_workspace_id():
    a, b = _two_workspaces()
    pa = _create_part(a, "A-part")
    # B's activity probe on A's part must 404, not return A's events.
    r = b.get(f"/api/parts/{pa}/activity")
    assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# BE2-021: assert_child_in_parent — foreign entry_id on project and order
# entry routes must 404, not silently operate on rows from another workspace.
# ---------------------------------------------------------------------------


def test_project_entry_foreign_entry_id_is_404():
    """PATCH / DELETE / match on a project entry must reject a foreign
    entry_id (one owned by workspace A) when called from workspace B.
    Without assert_child_in_parent the old db.get(ProjectEntry, id) path
    returned the row and wrote it — a cross-tenant write vector."""
    a, b = _two_workspaces()
    part_a = _create_part(a, "A-Part")
    part_b = _create_part(b, "B-Part")

    # A creates a project + entry.
    proj_a = a.post("/api/projects", json={"name": "PA"}).json()["data"]["id"]
    entry_a = a.post(
        f"/api/projects/{proj_a}/entries",
        json={"entry_type": "part", "part_id": part_a, "quantity": 1},
    ).json()["data"]["id"]

    # B creates their own project so the project_id in the URL is valid for B.
    proj_b = b.post("/api/projects", json={"name": "PB"}).json()["data"]["id"]

    # B uses their own project_id but A's entry_id — must 404 for all verbs.
    r = b.patch(
        f"/api/projects/{proj_b}/entries/{entry_a}",
        json={"quantity": 99},
    )
    assert r.status_code == 404, r.text

    r = b.delete(f"/api/projects/{proj_b}/entries/{entry_a}")
    assert r.status_code == 404, r.text

    r = b.post(
        f"/api/projects/{proj_b}/entries/{entry_a}/match",
        json={"part_id": part_b},
    )
    assert r.status_code == 404, r.text


def test_order_entry_foreign_entry_id_is_404():
    """PATCH / DELETE on an order entry must reject a foreign entry_id.
    Without assert_child_in_parent, db.get(OrderEntry, id) returned the
    cross-workspace row and let it be mutated."""
    a, b = _two_workspaces()
    # A creates an order + entry.
    order_a = a.post("/api/orders", json={"name": "OA"}).json()["data"]["id"]
    entry_a = a.post(
        f"/api/orders/{order_a}/entries",
        json={"name": "widget", "quantity_ordered": 5},
    ).json()["data"]["id"]

    # B creates their own order so the order_id in the URL is valid for B.
    order_b = b.post("/api/orders", json={"name": "OB"}).json()["data"]["id"]

    # B uses their own order_id but A's entry_id — must 404 for all verbs.
    r = b.patch(
        f"/api/orders/{order_b}/entries/{entry_a}",
        json={"quantity_ordered": 99},
    )
    assert r.status_code == 404, r.text

    r = b.delete(f"/api/orders/{order_b}/entries/{entry_a}")
    assert r.status_code == 404, r.text


def test_match_entry_foreign_part_id_is_404():
    """match_entry previously used db.get(Part, id) which skipped the
    workspace check. Confirm assert_in_workspace closes the gap: passing
    another workspace's part_id in the match payload must 404."""
    a, b = _two_workspaces()
    part_a = _create_part(a, "A-Part")

    # B creates a project + unmatched entry, then tries to match A's part.
    proj_b = b.post("/api/projects", json={"name": "PB"}).json()["data"]["id"]
    entry_b = b.post(
        f"/api/projects/{proj_b}/entries",
        json={"entry_type": "unmatched", "name": "mystery", "quantity": 1},
    ).json()["data"]["id"]

    r = b.post(
        f"/api/projects/{proj_b}/entries/{entry_b}/match",
        json={"part_id": part_a},
    )
    assert r.status_code == 404, r.text


def test_audit_log_does_not_leak_cross_workspace_rows():
    """GET /api/audit must only return rows belonging to the caller's
    workspace, even if both workspaces have audit rows."""
    a, b = _two_workspaces()
    # A generates an audit row via bulk-delete.
    pa = _create_part(a, "A-audit-part")
    r_del = a.post("/api/parts/bulk-delete", json={"part_ids": [pa]})
    assert r_del.status_code == 200, r_del.text

    # B reads their log — must contain zero rows from A.
    r_b = b.get("/api/audit")
    assert r_b.status_code == 200, r_b.text
    rows_b = r_b.json()["data"]
    # None of B's rows may reference A's part ID.
    a_part_ids_in_b = [
        row for row in rows_b
        if pa in (row.get("target_ids") or [])
    ]
    assert a_part_ids_in_b == [], (
        f"Workspace A's audit rows leaked into workspace B's log: {a_part_ids_in_b}"
    )


def test_sourcing_alert_evaluator_isolated_by_workspace(db, monkeypatch):
    """Alert evaluation must call sourcing with each alert's own workspace."""
    from app.domain.parts.models import Part
    from app.domain.sourcing import alerts_evaluator
    from app.domain.sourcing.models import SourcingAlert
    from app.domain.users.models import User
    from app.domain.workspaces.models import Workspace, WorkspaceMember

    def make_workspace() -> tuple[Workspace, User]:
        user = User(
            email=f"alert-isolation-{uuid.uuid4().hex[:8]}@example.com",
            name="Alert Isolation",
            password_hash="test",
        )
        db.add(user)
        db.flush()
        workspace = Workspace(
            name=f"alert-isolation-{uuid.uuid4().hex[:8]}",
            kind="organization",
            owner_user_id=user.id,
        )
        db.add(workspace)
        db.flush()
        db.add(
            WorkspaceMember(
                workspace_id=workspace.id,
                user_id=user.id,
                role="owner",
                status="active",
            )
        )
        db.flush()
        return workspace, user

    def make_part(workspace: Workspace, user: User) -> Part:
        part = Part(
            workspace_id=workspace.id,
            name=f"Shared MPN {uuid.uuid4().hex[:8]}",
            part_type="local",
            mpn="ALERT-SHARED-MPN",
            created_by=user.id,
            updated_by=user.id,
        )
        db.add(part)
        db.flush()
        return part

    workspace_a, user_a = make_workspace()
    workspace_b, user_b = make_workspace()
    part_a = make_part(workspace_a, user_a)
    part_b = make_part(workspace_b, user_b)
    db.add_all(
        [
            SourcingAlert(
                workspace_id=workspace_a.id,
                part_id=part_a.id,
                alert_type="back_in_stock",
                threshold={},
                notify_user_ids=[str(user_a.id)],
                last_evaluation_state={"had_stock": False},
                created_by=user_a.id,
            ),
            SourcingAlert(
                workspace_id=workspace_b.id,
                part_id=part_b.id,
                alert_type="back_in_stock",
                threshold={},
                notify_user_ids=[str(user_b.id)],
                last_evaluation_state={"had_stock": False},
                created_by=user_b.id,
            ),
        ]
    )
    db.flush()

    seen_workspace_ids = []

    def fake_search(*args, **kwargs):
        seen_workspace_ids.append(kwargs["workspace"].id)
        distributor = type("Distributor", (), {"stock": 1})()
        offer = type("Offer", (), {"distributors": [distributor]})()
        result = type("Result", (), {"offers": [offer]})()
        return type("SearchOut", (), {"results": [result]})()

    monkeypatch.setattr(alerts_evaluator.sourcing_service, "search", fake_search)
    monkeypatch.setattr(alerts_evaluator.mail, "send", lambda **kwargs: None)

    assert alerts_evaluator.evaluate_all_alerts(db) == 2

    assert set(seen_workspace_ids) == {workspace_a.id, workspace_b.id}


# ---------------------------------------------------------------------
# EDA libraries + per-part EDA config (phase 2)
# ---------------------------------------------------------------------


def _upload_eda_symbol(c: TestClient, entry: str = "R") -> dict:
    r = c.post(
        "/api/eda/symbols",
        files={
            "file": (
                f"{entry}.kicad_sym",
                _eda_symbol_text(entry).encode("utf-8"),
                "application/octet-stream",
            )
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["data"]


def test_eda_library_isolation_list_and_mutations_404_across_workspaces():
    a, b = _two_workspaces()
    symbol_a = _upload_eda_symbol(a, "A_Symbol")

    # B's listing must not include it.
    assert all(row["id"] != symbol_a["id"] for row in b.get("/api/eda/symbols").json()["data"])

    # Every mutation path 404s — never 403, which would confirm existence.
    assert b.patch(
        f"/api/eda/symbols/{symbol_a['id']}", json={"name": "stolen"}
    ).status_code == 404
    assert b.post(f"/api/eda/symbols/{symbol_a['id']}/archive").status_code == 404
    assert b.post(f"/api/eda/symbols/{symbol_a['id']}/restore").status_code == 404


def test_eda_stored_files_are_not_readable_across_workspaces():
    """The serve route keys on the ws_id in the PATH, so it has to match
    the caller's current workspace — otherwise knowing a sha is enough to
    read another tenant's library."""
    a, b = _two_workspaces()
    symbol_a = _upload_eda_symbol(a, "A_Symbol")
    ws_a = a.get("/api/auth/me").json()["data"]["workspaces"][0]["id"]

    assert b.get(f"/api/eda/files/{ws_a}/{symbol_a['sha256']}.kicad_sym").status_code == 404
    # And A can still read its own file, so the guard isn't just breaking it.
    assert a.get(f"/api/eda/files/{ws_a}/{symbol_a['sha256']}.kicad_sym").status_code == 200


def test_eda_footprint_model_link_rejects_foreign_datafile():
    a, b = _two_workspaces()
    r = a.post(
        "/api/eda/footprints",
        files={
            "file": (
                "F.kicad_mod",
                _eda_footprint_text("A_FP").encode("utf-8"),
                "application/octet-stream",
            )
        },
    )
    assert r.status_code == 201, r.text
    footprint_a = r.json()["data"]

    r = b.post(
        "/api/eda/datafiles",
        files={"file": ("b.step", _EDA_STEP_BYTES, "application/octet-stream")},
    )
    assert r.status_code == 201, r.text
    datafile_b = r.json()["data"]

    # A embedding B's datafile_id must 404, not silently create a link
    # that reaches across tenants.
    r = a.post(
        f"/api/eda/footprints/{footprint_a['id']}/models",
        json={"datafile_id": datafile_b["id"]},
    )
    assert r.status_code == 404, r.text
    assert a.get(f"/api/eda/footprints/{footprint_a['id']}/models").json()["data"] == []


def test_part_eda_config_isolated_across_workspaces():
    a, b = _two_workspaces()
    part_a = _create_part(a, "A-part")
    assert a.put(f"/api/parts/{part_a}/eda", json={"value": "10k"}).status_code == 200

    assert b.get(f"/api/parts/{part_a}/eda").status_code == 404
    assert b.put(f"/api/parts/{part_a}/eda", json={"value": "stolen"}).status_code == 404
    assert b.delete(f"/api/parts/{part_a}/eda").status_code == 404
    # The refused writes left A's config untouched.
    assert a.get(f"/api/parts/{part_a}/eda").json()["data"]["value"] == "10k"


def test_part_eda_rejects_foreign_symbol_and_footprint_ids():
    """Without ws-validation on the body ids, a B caller could point its
    own part at A's hosted symbol — binding a row across tenants and
    leaking an existence oracle."""
    a, b = _two_workspaces()
    symbol_a = _upload_eda_symbol(a, "A_Symbol")
    part_b = _create_part(b, "B-part")

    r = b.put(f"/api/parts/{part_b}/eda", json={"symbol_id": symbol_a["id"]})
    assert r.status_code == 404, r.text
    assert b.get(f"/api/parts/{part_b}/eda").json()["data"] is None
