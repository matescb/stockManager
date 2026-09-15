"""`/kicad-api/v1` — the KiCad HTTP library.

Three contracts are pinned here, and each of them breaks a real KiCad
install if it drifts:

* **Auth is header-only, and rejections collapse to 404.** KiCad
  accepts nothing but a 200, so nothing is gained by telling a bad
  token from an unknown part. The rate limiter's 429 is the one
  deliberate exception — it is raised before the router runs, needs no
  valid credential to reach, and carries `Retry-After`. A session
  cookie must never authenticate this surface: that would put a
  CSRF-exempt, envelope-free API behind the browser's ambient
  credential.
* **Every scalar is a string.** KiCad's parser reads `"True"`, not
  `true`, and `"16"`, not `16`. `test_no_non_string_scalars_anywhere`
  walks the documents rather than checking a field list, so a new field
  cannot slip an int through.
* **The naming contract.** `PCM_SM_<slug>:<entry>` where the slug comes from
  the SYMBOL's category, not the part's. Phase 6 generates the library
  files these references name; if the two disagree KiCad reports a
  broken symbol for every part in the workspace.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select

import app.core.ratelimit as _ratelimit_mod
from app.core.time import utcnow
from app.domain.custom_fields.models import CustomField
from app.domain.eda import kicad_library, kicad_refs
from app.domain.tokens.models import ApiToken
from app.domain.workspaces.models import WorkspaceMember
from app.main import app
from tests._factories import create_part, signup_user

ROOT = "/kicad-api/v1/"
CATEGORIES = "/kicad-api/v1/categories.json"


def _parts_in(category_id: str) -> str:
    return f"/kicad-api/v1/parts/category/{category_id}.json"


def _part(part_id: str) -> str:
    return f"/kicad-api/v1/parts/{part_id}.json"


# ---------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------


def _symbol_text(name: str) -> str:
    return (
        f'(symbol "{name}" (in_bom yes) (on_board yes)\n'
        f'  (property "Reference" "R" (at 0 0 0))\n'
        f'  (property "Value" "{name}" (at 0 0 0))\n'
        f")\n"
    )


def _footprint_text(name: str) -> str:
    return (
        f'(footprint "{name}" (layer "F.Cu")\n'
        f'  (descr "test")\n'
        f'  (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu"))\n'
        f")\n"
    )


class Tenant:
    """One signed-up workspace: its session client and a KiCad client.

    `kicad` carries the PAT and NO session cookie, which is what the
    real client looks like; `session` is the browser.
    """

    def __init__(self, read_only: bool = True) -> None:
        self.session = TestClient(app)
        self.email = f"u-{uuid.uuid4().hex[:8]}@example.com"
        signed_up = signup_user(self.session, email=self.email)
        self.workspace_id = uuid.UUID(signed_up.json()["data"]["workspace_id"])
        self.token = self.mint(read_only=read_only)
        self.kicad = _token_client(self.token)

    def mint(self, *, read_only: bool = True, expires_in_days: int | None = None) -> str:
        body: dict[str, object] = {"label": f"kicad {uuid.uuid4().hex[:6]}"}
        body["read_only"] = read_only
        if expires_in_days is not None:
            body["expires_in_days"] = expires_in_days
        r = self.session.post("/api/tokens", json=body)
        assert r.status_code == 201, r.text
        return r.json()["data"]["token"]

    # -- fixture builders (all through the HTTP API, per house rules) --

    def category(self, name: str, **extra) -> dict:
        r = self.session.post("/api/categories", json={"name": name, **extra})
        assert r.status_code in (200, 201), r.text
        return r.json()["data"]

    def symbol(self, entry: str, *, category_id: str | None = None) -> dict:
        data = {"category_id": category_id} if category_id else {}
        r = self.session.post(
            "/api/eda/symbols",
            files={
                "file": (
                    f"{entry}.kicad_sym",
                    _symbol_text(entry).encode(),
                    "application/octet-stream",
                )
            },
            data=data,
        )
        assert r.status_code in (200, 201), r.text
        return r.json()["data"]

    def footprint(self, entry: str, *, category_id: str | None = None) -> dict:
        data = {"category_id": category_id} if category_id else {}
        r = self.session.post(
            "/api/eda/footprints",
            files={
                "file": (
                    f"{entry}.kicad_mod",
                    _footprint_text(entry).encode(),
                    "application/octet-stream",
                )
            },
            data=data,
        )
        assert r.status_code in (200, 201), r.text
        return r.json()["data"]

    def configure(self, part_id: str, **body) -> dict:
        r = self.session.put(f"/api/parts/{part_id}/eda", json=body)
        assert r.status_code == 200, r.text
        return r.json()["data"]


def _token_client(token: str) -> TestClient:
    """A client that authenticates the way KiCad does: `Token <pat>`,
    and no session cookie at all."""
    c = TestClient(app)
    c.headers["Authorization"] = f"Token {token}"
    return c


@pytest.fixture
def ws(db) -> Tenant:
    return Tenant()


@pytest.fixture
def other(db) -> Tenant:
    return Tenant()


# ---------------------------------------------------------------------
# Authentication — every failure is the same 404
# ---------------------------------------------------------------------


ALL_PATHS = [ROOT, CATEGORIES, _parts_in("uncategorized"), _part(str(uuid.uuid4()))]


@pytest.mark.parametrize("path", ALL_PATHS)
def test_no_header_is_404(db, path: str):
    assert TestClient(app).get(path).status_code == 404


@pytest.mark.parametrize(
    "header",
    [
        "Token garbage",
        "Token smk_deadbeef.nope",
        "Bearer ",
        "Basic aGk6dGhlcmU=",
        "smk_no_scheme_at_all",
    ],
)
def test_bad_credentials_are_404(db, header: str):
    c = TestClient(app)
    c.headers["Authorization"] = header
    assert c.get(CATEGORIES).status_code == 404


def test_valid_read_only_token_is_200(ws: Tenant):
    assert ws.kicad.get(CATEGORIES).status_code == 200


def test_valid_full_token_is_200(db):
    full = Tenant(read_only=False)
    assert full.kicad.get(CATEGORIES).status_code == 200


def test_session_cookie_without_header_is_404(ws: Tenant):
    """The browser's own client — cookie set, no Authorization — must
    not reach this surface. It is CSRF-exempt and envelope-free; the
    ambient credential has no business here."""
    assert ws.session.get(CATEGORIES).status_code == 404


def test_revoked_token_is_404(ws: Tenant, db):
    token = ws.mint()
    client = _token_client(token)
    assert client.get(CATEGORIES).status_code == 200

    row_id = uuid.UUID(token.split("_", 1)[1].split(".", 1)[0])
    r = ws.session.post(f"/api/tokens/{row_id}/revoke")
    assert r.status_code == 200, r.text
    assert client.get(CATEGORIES).status_code == 404


def test_expired_token_is_404(ws: Tenant, db):
    token = ws.mint(expires_in_days=1)
    client = _token_client(token)
    assert client.get(CATEGORIES).status_code == 200

    row_id = uuid.UUID(token.split("_", 1)[1].split(".", 1)[0])
    row = db.get(ApiToken, row_id)
    row.expires_at = utcnow() - timedelta(seconds=1)
    db.flush()
    assert client.get(CATEGORIES).status_code == 404


def test_token_dies_with_its_membership(ws: Tenant, db):
    """A departed member's token stops working — the membership re-check
    in `deps._authenticate_api_token` is on this path too."""
    assert ws.kicad.get(CATEGORIES).status_code == 200
    member = db.execute(select(WorkspaceMember)).scalars().first()
    member.status = "removed"
    db.flush()
    assert ws.kicad.get(CATEGORIES).status_code == 404


# ---------------------------------------------------------------------
# Rate limiting — must apply to requests that never authenticate
# ---------------------------------------------------------------------


@pytest.fixture
def limiter_enabled():
    """slowapi is off outside prod so the suite can hammer endpoints.

    Turning it on for one test is the only way to exercise the wiring,
    and the bucket store is process-global, so it is reset on both
    sides.
    """
    original = _ratelimit_mod.limiter.enabled
    _ratelimit_mod.limiter.enabled = True
    _ratelimit_mod.limiter.reset()
    try:
        yield
    finally:
        _ratelimit_mod.limiter.enabled = original
        _ratelimit_mod.limiter.reset()


def _flood(client: TestClient, path: str, attempts: int, rotate: bool = False) -> int:
    """Hammer *path* and return the status of the first non-404 answer."""
    for index in range(attempts):
        if rotate:
            client.headers["Authorization"] = f"Token smk_{uuid.uuid4().hex}.wrong{index}"
        r = client.get(path)
        if r.status_code != 404:
            return r.status_code
    return 404


def test_invalid_token_flood_is_rate_limited(db, limiter_enabled):
    """A credential-stuffing flood must cost the attacker something.

    Before this was restructured the token was resolved in a FastAPI
    dependency, which runs BEFORE slowapi's check — so every invalid
    request 404'd without ever touching a bucket and the limits were
    decorative. The routes now resolve the token in their body.
    """
    client = TestClient(app)
    client.headers["Authorization"] = "Token smk_deadbeef.not-a-real-secret"
    assert _flood(client, CATEGORIES, 200) == 429


def test_rotating_the_token_does_not_dodge_the_ip_cap(db, limiter_enabled):
    """The token bucket keys on a digest of the presented credential, so
    a fresh token per request lands in a fresh bucket every time. The
    parallel per-IP cap is what closes that."""
    client = TestClient(app)
    assert _flood(client, CATEGORIES, 400, rotate=True) == 429


def test_a_valid_token_is_not_throttled_by_a_neighbours_flood(ws: Tenant, limiter_enabled):
    """Buckets are per credential, so one abusive caller must not take
    the library down for everyone else on the same egress IP.

    Only the token bucket is asserted here: the IP cap is deliberately
    shared, and a flood large enough to trip it would take this
    workspace down too — which is the trade the cap exists to make.
    """
    noisy = TestClient(app)
    noisy.headers["Authorization"] = "Token smk_deadbeef.not-a-real-secret"
    assert _flood(noisy, CATEGORIES, 130) == 429
    assert ws.kicad.get(CATEGORIES).status_code == 200


def test_failure_body_is_small_json(db):
    r = TestClient(app).get(CATEGORIES)
    assert r.status_code == 404
    body = r.json()
    assert body["code"] == "kicad.not_found"
    # Nothing about tokens, workspaces or parts leaks into the message.
    assert body["status"]["message"] == "not found"


# ---------------------------------------------------------------------
# Document shapes
# ---------------------------------------------------------------------


def test_root_document_is_exact(ws: Tenant):
    r = ws.kicad.get(ROOT)
    assert r.status_code == 200, r.text
    assert r.json() == {"categories": "", "parts": ""}


def test_root_is_served_at_the_trailing_slash_kicad_composes(ws: Tenant):
    """KiCad builds this URL as `root_url` + `/v1/`, so the slash form
    must be the one that answers — not a redirect to a slashless
    canonical path that a strict client may not follow."""
    r = ws.kicad.get(ROOT, follow_redirects=False)
    assert r.status_code == 200, f"{r.status_code}: {r.text}"


def _walk_scalars(node, path: str = "$"):
    """Yield (path, value) for every leaf in a JSON document."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _walk_scalars(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk_scalars(value, f"{path}[{index}]")
    else:
        yield path, node


def test_no_non_string_scalars_anywhere(ws: Tenant):
    """Walk every document; a bool, int, float or null anywhere is a bug.

    KiCad's parser is not forgiving: `false` where it expects `"False"`
    takes the whole library down, and the failure surfaces as an empty
    chooser with no error.
    """
    category = ws.category("Passives", description="R, C, L")
    part_id = create_part(
        ws.session,
        "R 10k",
        category_id=category["id"],
        mpn=f"R10K-{uuid.uuid4().hex[:6]}",
        manufacturer="Yageo",
        description="10k 1% 0402",
    )
    symbol = ws.symbol("R", category_id=category["id"])
    ws.configure(
        part_id,
        symbol_id=symbol["id"],
        keywords="resistor passive",
        footprint_filters=["R_*"],
        exclude_from_bom=True,
        exclude_from_board=False,
        exclude_from_sim=True,
    )

    documents = {
        ROOT: ws.kicad.get(ROOT).json(),
        CATEGORIES: ws.kicad.get(CATEGORIES).json(),
        "listing": ws.kicad.get(_parts_in(category["id"])).json(),
        "detail": ws.kicad.get(_part(part_id)).json(),
    }
    for label, document in documents.items():
        for path, value in _walk_scalars(document):
            assert isinstance(value, str), (
                f"{label} {path} is {type(value).__name__} ({value!r}); "
                "every scalar KiCad reads must be a string"
            )


def test_exclusion_flags_are_stringified_booleans(ws: Tenant):
    part_id = create_part(ws.session, "Testpoint")
    ws.configure(
        part_id,
        symbol_ref_external="Device:R",
        exclude_from_bom=True,
        exclude_from_board=True,
        exclude_from_sim=False,
    )
    document = ws.kicad.get(_part(part_id)).json()
    assert document["exclude_from_bom"] == "True"
    assert document["exclude_from_board"] == "True"
    assert document["exclude_from_sim"] == "False"


def test_defaults_when_the_part_has_no_eda_row(ws: Tenant):
    """A part that inherits everything from its category still answers
    with the documented defaults rather than omitting the flags."""
    category = ws.category("Diodes", default_symbol_ref="Device:D")
    part_id = create_part(ws.session, "1N4148", category_id=category["id"])

    document = ws.kicad.get(_part(part_id)).json()
    assert document["symbolIdStr"] == "Device:D"
    assert document["exclude_from_bom"] == "False"
    assert document["exclude_from_board"] == "False"
    assert document["exclude_from_sim"] == "True"
    # `value` falls back to the part name and stays visible.
    assert document["fields"]["value"] == {"value": "1N4148"}


def test_listing_rows_carry_the_full_part_document(ws: Tenant):
    """A listing row IS the detail document.

    KiCad 9.0 reads only `id`/`name`/`description` from a listing and
    then fetches each part; master (10+) keeps a full-shape row as the
    cached detail and skips that fetch. Emitting the full shape serves
    both, and asserting the two endpoints agree byte-for-byte is what
    stops the shapes drifting apart.
    """
    category = ws.category("Mixed")
    bare = create_part(ws.session, "Bare", category_id=category["id"])
    rich = create_part(
        ws.session,
        "Rich",
        category_id=category["id"],
        mpn=f"RICH-{uuid.uuid4().hex[:6]}",
        manufacturer="ACME",
        description="described",
    )
    for part_id in (bare, rich):
        ws.configure(part_id, symbol_ref_external="Device:R")

    rows = ws.kicad.get(_parts_in(category["id"])).json()
    assert len(rows) == 2
    required = {
        "id",
        "name",
        "symbolIdStr",
        "description",
        "keywords",
        "exclude_from_bom",
        "exclude_from_board",
        "exclude_from_sim",
        "fields",
    }
    for row in rows:
        assert required <= set(row.keys()), f"listing row is missing {required - set(row)}"
        assert row == ws.kicad.get(_part(row["id"])).json()


def test_every_document_carries_a_symbol_and_non_empty_fields(ws: Tenant):
    """KiCad 9.0 reads `symbolIdStr` and indexes into `fields` without
    guarding either. A document missing one is a crash, not a fallback,
    so both are asserted on the sparsest part we can build."""
    category = ws.category("Passives", default_symbol_ref="Device:R")
    sparse = create_part(ws.session, "No config at all", category_id=category["id"])
    configured = create_part(ws.session, "Configured", category_id=category["id"])
    ws.configure(configured, symbol_ref_external="Device:C")

    documents = ws.kicad.get(_parts_in(category["id"])).json()
    documents.append(ws.kicad.get(_part(sparse)).json())
    assert len(documents) == 3
    for document in documents:
        assert document["symbolIdStr"], document
        assert document["fields"], document
        # `value` is always there — it falls back to the part name.
        assert document["fields"]["value"]["value"]


# ---------------------------------------------------------------------
# The naming contract + resolution order
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "slug,stem,nickname",
    [
        ("resistors", "SM_resistors", "PCM_SM_resistors"),
        (None, "SM_uncategorized", "PCM_SM_uncategorized"),
        ("", "SM_uncategorized", "PCM_SM_uncategorized"),
    ],
)
def test_stem_names_the_file_and_nickname_names_the_reference(
    slug: str | None, stem: str, nickname: str
):
    """Phase 6 writes `<stem>.kicad_sym`; KiCad's Plugin & Content
    Manager registers it under `PCM_` + that stem, and only that
    nickname resolves. A reference built on the bare stem points at no
    registered library, which KiCad shows as a broken symbol on every
    part using it — so the two functions must stay exactly one prefix
    apart.
    """
    assert kicad_refs.package_stem(slug) == stem
    assert kicad_refs.library_nickname(slug) == nickname
    assert nickname == kicad_refs.PCM_NICKNAME_PREFIX + stem
    # References are built on the nickname, never the stem.
    assert kicad_refs.entry_ref("R_0402", slug) == f"{nickname}:R_0402"


def test_symbol_and_footprint_libraries_share_one_nickname():
    """KiCad keeps symbol and footprint libraries in separate tables, so
    one name in both is unambiguous — and it is what a user sees in
    either chooser."""
    assert kicad_refs.symbol_lib_nickname("passives") == "PCM_SM_passives"
    assert kicad_refs.footprint_lib_nickname("passives") == "PCM_SM_passives"


def test_hosted_symbol_uses_the_symbols_own_category(ws: Tenant):
    """THE contract phase 6 has to match.

    The part is filed under "Passives" and its symbol under "Generic";
    the reference must name the SYMBOL's library. Getting this backwards
    is the single most likely way to break the generated libraries, so
    the two categories are deliberately different here.
    """
    part_category = ws.category("Passives", library_slug="passives")
    symbol_category = ws.category("Generic Symbols", library_slug="generic")
    part_id = create_part(ws.session, "R 4k7", category_id=part_category["id"])
    symbol = ws.symbol("R_Small", category_id=symbol_category["id"])
    ws.configure(part_id, symbol_id=symbol["id"])

    document = ws.kicad.get(_part(part_id)).json()
    # The literal, so a change to the format is visible in the diff; and
    # the shared helper, so the route can't drift from what phase 6 will
    # call.
    assert document["symbolIdStr"] == "PCM_SM_generic:R_Small"
    assert document["symbolIdStr"] == kicad_refs.entry_ref(symbol["name"], "generic")


def test_symbol_without_a_category_lands_in_uncategorized(ws: Tenant):
    part_id = create_part(ws.session, "Odd part")
    symbol = ws.symbol("ODD")
    ws.configure(part_id, symbol_id=symbol["id"])

    document = ws.kicad.get(_part(part_id)).json()
    assert document["symbolIdStr"] == "PCM_SM_uncategorized:ODD"


def test_external_ref_wins_over_the_category_default(ws: Tenant):
    category = ws.category("Passives", default_symbol_ref="PCM_SM_never:Used")
    part_id = create_part(ws.session, "R", category_id=category["id"])
    ws.configure(part_id, symbol_ref_external="Device:R")

    assert ws.kicad.get(_part(part_id)).json()["symbolIdStr"] == "Device:R"


def test_hosted_symbol_wins_over_the_category_default(ws: Tenant):
    category = ws.category("Passives", default_symbol_ref="Device:R")
    part_id = create_part(ws.session, "R", category_id=category["id"])
    symbol = ws.symbol("R_Hosted", category_id=category["id"])
    ws.configure(part_id, symbol_id=symbol["id"])

    assert ws.kicad.get(_part(part_id)).json()["symbolIdStr"] == "PCM_SM_passives:R_Hosted"


def test_archived_hosted_symbol_falls_back_to_the_category_default(ws: Tenant):
    """Phase 6 packages active rows only, so a reference to an archived
    symbol would name an entry that isn't in the generated library."""
    category = ws.category("Passives", default_symbol_ref="Device:R")
    part_id = create_part(ws.session, "R", category_id=category["id"])
    symbol = ws.symbol("R_Hosted", category_id=category["id"])
    ws.configure(part_id, symbol_id=symbol["id"])
    assert ws.session.post(f"/api/eda/symbols/{symbol['id']}/archive").status_code == 200

    assert ws.kicad.get(_part(part_id)).json()["symbolIdStr"] == "Device:R"


def test_footprint_resolution_mirrors_the_symbol(ws: Tenant):
    part_category = ws.category("Passives", library_slug="passives")
    fp_category = ws.category("Land Patterns", library_slug="land")
    part_id = create_part(ws.session, "R 0402", category_id=part_category["id"])
    symbol = ws.symbol("R", category_id=part_category["id"])
    footprint = ws.footprint("R_0402", category_id=fp_category["id"])
    ws.configure(part_id, symbol_id=symbol["id"], footprint_id=footprint["id"])

    fields = ws.kicad.get(_part(part_id)).json()["fields"]
    assert fields["footprint"] == {"value": "PCM_SM_land:R_0402", "visible": "False"}


def test_footprint_falls_back_to_the_category_default(ws: Tenant):
    category = ws.category("Passives", default_footprint_ref="Resistor_SMD:R_0402")
    part_id = create_part(ws.session, "R", category_id=category["id"])
    ws.configure(part_id, symbol_ref_external="Device:R")

    fields = ws.kicad.get(_part(part_id)).json()["fields"]
    assert fields["footprint"]["value"] == "Resistor_SMD:R_0402"


def test_footprint_key_absent_when_nothing_resolves(ws: Tenant):
    part_id = create_part(ws.session, "R")
    ws.configure(part_id, symbol_ref_external="Device:R")

    assert "footprint" not in ws.kicad.get(_part(part_id)).json()["fields"]


def test_part_without_a_symbol_is_invisible(ws: Tenant):
    """No symbol means the chooser would offer a part that can't be
    placed — so it is absent from the listing and 404s on detail."""
    category = ws.category("Passives")
    placed = create_part(ws.session, "Has symbol", category_id=category["id"])
    ws.configure(placed, symbol_ref_external="Device:R")
    orphan = create_part(ws.session, "No symbol", category_id=category["id"])

    rows = ws.kicad.get(_parts_in(category["id"])).json()
    assert [row["id"] for row in rows] == [placed]
    assert ws.kicad.get(_part(orphan)).status_code == 404


def test_archived_part_is_invisible(ws: Tenant):
    category = ws.category("Passives")
    part_id = create_part(ws.session, "Retired", category_id=category["id"])
    ws.configure(part_id, symbol_ref_external="Device:R")
    assert ws.session.post(f"/api/parts/{part_id}/archive").status_code == 200

    assert ws.kicad.get(_parts_in(category["id"])).json() == []
    assert ws.kicad.get(_part(part_id)).status_code == 404


# ---------------------------------------------------------------------
# Categories listing
# ---------------------------------------------------------------------


def test_categories_are_ordered_and_described(ws: Tenant):
    """Depth-first by `(sort_order, name)`, each subcategory named by
    its full path — the document has no other field to nest in."""
    second = ws.category("Second", sort_order=2, description="two")
    ws.category("First", sort_order=1)
    ws.category("Leaf", parent_id=second["id"], description="under two")

    rows = ws.kicad.get(CATEGORIES).json()
    assert [row["name"] for row in rows] == ["First", "Second", "Second / Leaf"]
    # A category with no description still carries the key, empty.
    assert rows[0]["description"] == ""
    assert rows[1]["description"] == "two"
    # The path is in `name` only — `description` stays the row's own.
    assert rows[2]["description"] == "under two"


def test_uncategorized_appears_only_when_an_eligible_part_needs_it(ws: Tenant):
    ws.category("Passives")
    assert _names(ws.kicad.get(CATEGORIES).json()) == ["Passives"]

    # A part with no category but no symbol either: still nothing to show.
    orphan = create_part(ws.session, "No symbol")
    assert _names(ws.kicad.get(CATEGORIES).json()) == ["Passives"]

    ws.configure(orphan, symbol_ref_external="Device:R")
    rows = ws.kicad.get(CATEGORIES).json()
    assert _names(rows) == ["Passives", kicad_library.UNCATEGORIZED_NAME]
    assert rows[-1]["id"] == kicad_library.UNCATEGORIZED_ID


def _names(rows: list[dict]) -> list[str]:
    return [row["name"] for row in rows]


def test_uncategorized_listing_returns_the_uncategorized_parts(ws: Tenant):
    category = ws.category("Passives")
    filed = create_part(ws.session, "Filed", category_id=category["id"])
    loose = create_part(ws.session, "Loose")
    for part_id in (filed, loose):
        ws.configure(part_id, symbol_ref_external="Device:R")

    rows = ws.kicad.get(_parts_in(kicad_library.UNCATEGORIZED_ID)).json()
    assert [row["id"] for row in rows] == [loose]


def test_archived_category_is_not_addressable(ws: Tenant):
    category = ws.category("Retired")
    assert ws.session.post(f"/api/categories/{category['id']}/archive").status_code == 200

    assert _names(ws.kicad.get(CATEGORIES).json()) == []
    assert ws.kicad.get(_parts_in(category["id"])).status_code == 404


# ---------------------------------------------------------------------
# Archiving a category must not orphan its parts
# ---------------------------------------------------------------------


def test_archiving_a_category_moves_its_parts_to_uncategorized(ws: Tenant):
    """Archiving a category used to strand every part in it.

    The category vanished from `categories.json`, and its parts still
    carried its id — so they were in no reachable bucket, while their
    detail kept answering 200. Placeable, findable nowhere. They now
    surface under the synthetic bucket instead.
    """
    category = ws.category("Retired")
    part_id = create_part(ws.session, "Stranded", category_id=category["id"])
    ws.configure(part_id, symbol_ref_external="Device:R")
    assert [row["id"] for row in ws.kicad.get(_parts_in(category["id"])).json()] == [part_id]

    assert ws.session.post(f"/api/categories/{category['id']}/archive").status_code == 200

    rows = ws.kicad.get(CATEGORIES).json()
    assert _names(rows) == [kicad_library.UNCATEGORIZED_NAME]
    listed = ws.kicad.get(_parts_in(kicad_library.UNCATEGORIZED_ID)).json()
    assert [row["id"] for row in listed] == [part_id]
    assert ws.kicad.get(_part(part_id)).status_code == 200


def test_an_archived_categorys_defaults_stop_applying(ws: Tenant):
    """The other half: a bucket KiCad can't see must not keep supplying
    references. A part whose only symbol came from the archived
    category's default has nothing left and drops out entirely."""
    category = ws.category(
        "Retired",
        default_symbol_ref="Device:R",
        default_footprint_ref="Resistor_SMD:R_0402",
        footprint_filters=["R_*"],
    )
    inherits_all = create_part(ws.session, "Inherits", category_id=category["id"])
    has_own = create_part(ws.session, "Own symbol", category_id=category["id"])
    ws.configure(has_own, symbol_ref_external="Device:C")
    assert ws.kicad.get(_part(inherits_all)).status_code == 200

    assert ws.session.post(f"/api/categories/{category['id']}/archive").status_code == 200

    # Nothing to resolve a symbol from any more.
    assert ws.kicad.get(_part(inherits_all)).status_code == 404
    listed = ws.kicad.get(_parts_in(kicad_library.UNCATEGORIZED_ID)).json()
    assert [row["id"] for row in listed] == [has_own]
    # And the part that survives no longer inherits the dead category's
    # footprint or filters.
    document = ws.kicad.get(_part(has_own)).json()
    assert "footprint" not in document["fields"]
    assert "footprint_filters" not in document


def test_archived_hosted_symbol_with_no_fallback_drops_the_part(ws: Tenant):
    """The gap case between the two archive rules: the hosted symbol is
    archived AND there is no category default behind it."""
    category = ws.category("Passives")
    part_id = create_part(ws.session, "R", category_id=category["id"])
    symbol = ws.symbol("R_Hosted", category_id=category["id"])
    ws.configure(part_id, symbol_id=symbol["id"])
    assert ws.kicad.get(_part(part_id)).status_code == 200

    assert ws.session.post(f"/api/eda/symbols/{symbol['id']}/archive").status_code == 200

    assert ws.kicad.get(_parts_in(category["id"])).json() == []
    assert ws.kicad.get(_part(part_id)).status_code == 404


def test_a_symbol_in_an_archived_category_is_referenced_as_uncategorized(ws: Tenant):
    """The archive rule reaches the naming contract too: phase 6 files
    an entry whose category is archived under the uncategorized
    library, so the reference has to name that library."""
    symbol_category = ws.category("Retired symbols", library_slug="retired")
    part_id = create_part(ws.session, "R")
    symbol = ws.symbol("R_Small", category_id=symbol_category["id"])
    ws.configure(part_id, symbol_id=symbol["id"])
    assert ws.kicad.get(_part(part_id)).json()["symbolIdStr"] == "PCM_SM_retired:R_Small"

    archived = ws.session.post(f"/api/categories/{symbol_category['id']}/archive")
    assert archived.status_code == 200, archived.text

    assert ws.kicad.get(_part(part_id)).json()["symbolIdStr"] == "PCM_SM_uncategorized:R_Small"


def test_uncategorized_probe_stops_at_the_first_eligible_part(ws: Tenant, monkeypatch):
    """`categories.json` only needs to know WHETHER an uncategorized
    part exists. Building a row object for all of them to answer that
    is work that scales with the workspace, so the generator must
    short-circuit — the docstring says it does, and this is what makes
    that true rather than aspirational."""
    for index in range(8):
        part_id = create_part(ws.session, f"Loose {index}")
        ws.configure(part_id, symbol_ref_external="Device:R")

    built = 0
    original = kicad_library._PartRow

    def counting(*args, **kwargs):
        nonlocal built
        built += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(kicad_library, "_PartRow", counting)
    assert _names(ws.kicad.get(CATEGORIES).json()) == [kicad_library.UNCATEGORIZED_NAME]
    assert built == 1, f"built {built} rows to answer a yes/no question"


def test_unknown_category_is_404(ws: Tenant):
    assert ws.kicad.get(_parts_in(str(uuid.uuid4()))).status_code == 404
    assert ws.kicad.get(_parts_in("not-a-uuid")).status_code == 404


def test_unknown_part_is_404(ws: Tenant):
    assert ws.kicad.get(_part(str(uuid.uuid4()))).status_code == 404
    assert ws.kicad.get(_part("not-a-uuid")).status_code == 404


# ---------------------------------------------------------------------
# Fields
# ---------------------------------------------------------------------


def test_metadata_fields_are_hidden_and_value_is_not(ws: Tenant):
    part_id = create_part(
        ws.session,
        "R 10k",
        mpn=f"RC0402-{uuid.uuid4().hex[:6]}",
        manufacturer="Yageo",
        internal_part_number="IPN-0001",
        description="10k 1%",
    )
    ws.configure(
        part_id, symbol_ref_external="Device:R", value="10k", keywords="resistor"
    )

    fields = ws.kicad.get(_part(part_id)).json()["fields"]
    assert fields["value"] == {"value": "10k"}, "the schematic value stays visible"
    for key in ("MPN", "Manufacturer", "IPN", "description", "keywords", "StockManager"):
        assert fields[key]["visible"] == "False", key
    assert fields["Manufacturer"]["value"] == "Yageo"
    assert fields["IPN"]["value"] == "IPN-0001"
    assert fields["StockManager"]["value"].endswith(f"/parts/{part_id}")


def test_empty_metadata_fields_are_omitted(ws: Tenant):
    """An empty KiCad field is a blank property drawn on every instance
    of the symbol, not an absence."""
    part_id = create_part(ws.session, "Bare")
    ws.configure(part_id, symbol_ref_external="Device:R")

    fields = ws.kicad.get(_part(part_id)).json()["fields"]
    for key in ("MPN", "Manufacturer", "IPN", "description", "keywords", "datasheet"):
        assert key not in fields, key


def _set_datasheet(ws: Tenant, db, part_id: str, value: str) -> None:
    """Write the provider-owned `datasheet_url` custom field directly.

    `POST /api/custom-fields` refuses this key on purpose — it is
    provider-managed (`custom_field.reserved_key`), written only by the
    part import. There is no HTTP path to it that doesn't involve
    standing up a fake provider, so this is one of the few places a
    direct insert beats the factory rule.
    """
    db.add(
        CustomField(
            workspace_id=ws.workspace_id,
            object_type="part",
            object_id=uuid.UUID(part_id),
            key="datasheet_url",
            value=value,
            source="provider",
        )
    )
    db.flush()


def test_datasheet_comes_from_the_custom_field(ws: Tenant, db):
    part_id = create_part(ws.session, "R")
    ws.configure(part_id, symbol_ref_external="Device:R")
    _set_datasheet(ws, db, part_id, "https://example.test/r.pdf")

    fields = ws.kicad.get(_part(part_id)).json()["fields"]
    assert fields["datasheet"] == {
        "value": "https://example.test/r.pdf",
        "visible": "False",
    }


def test_locally_stored_datasheet_is_made_absolute(ws: Tenant, db):
    """A downloaded datasheet is stored as an app-relative path. KiCad
    opens the value in a browser with no notion of our origin, so a
    relative one would be a dead link."""
    part_id = create_part(ws.session, "R")
    ws.configure(part_id, symbol_ref_external="Device:R")
    _set_datasheet(ws, db, part_id, "/api/parts/assets/ws/abc.pdf")

    value = ws.kicad.get(_part(part_id)).json()["fields"]["datasheet"]["value"]
    assert value.startswith("http")
    assert value.endswith("/api/parts/assets/ws/abc.pdf")


@pytest.mark.parametrize(
    "value",
    [
        "file:///etc/passwd",
        "javascript:alert(1)",
        r"\\evil.host\share\payload.lnk",
        "C:\\Windows\\System32\\calc.exe",
        "ftp://example.test/r.pdf",
    ],
)
def test_non_http_datasheet_values_are_not_passed_through(ws: Tenant, db, value: str):
    """KiCad hands this value to the OS URL handler when the user clicks
    "Datasheet". The value originates in provider data we don't control,
    so anything that isn't `http(s)` (or our own relative path) is
    dropped rather than turned into a click-to-open on the engineer's
    machine."""
    part_id = create_part(ws.session, "R")
    ws.configure(part_id, symbol_ref_external="Device:R")
    _set_datasheet(ws, db, part_id, value)

    assert "datasheet" not in ws.kicad.get(_part(part_id)).json()["fields"]


def test_footprint_filters_override_the_category(ws: Tenant):
    category = ws.category("Passives", footprint_filters=["C_*"])
    part_id = create_part(ws.session, "R", category_id=category["id"])
    ws.configure(part_id, symbol_ref_external="Device:R", footprint_filters=["R_*"])

    assert ws.kicad.get(_part(part_id)).json()["footprint_filters"] == ["R_*"]
    assert ws.kicad.get(_parts_in(category["id"])).json()[0]["footprint_filters"] == ["R_*"]


def test_footprint_filters_inherit_from_the_category(ws: Tenant):
    category = ws.category("Passives", footprint_filters=["C_*"])
    part_id = create_part(ws.session, "C", category_id=category["id"])
    ws.configure(part_id, symbol_ref_external="Device:C")

    assert ws.kicad.get(_part(part_id)).json()["footprint_filters"] == ["C_*"]


def test_footprint_filters_omitted_from_detail_when_empty(ws: Tenant):
    part_id = create_part(ws.session, "R")
    ws.configure(part_id, symbol_ref_external="Device:R")

    assert "footprint_filters" not in ws.kicad.get(_part(part_id)).json()


# ---------------------------------------------------------------------
# SPICE
# ---------------------------------------------------------------------


def _upload_spice(ws: Tenant, name: str) -> dict:
    r = ws.session.post(
        "/api/eda/datafiles",
        files={
            "file": (
                f"{name}.lib",
                b".subckt MYPART 1 2\nR1 1 2 10k\n.ends\n",
                "application/octet-stream",
            )
        },
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]


def test_sim_fields_appear_when_simulation_is_enabled(ws: Tenant):
    datafile = _upload_spice(ws, "mypart")
    part_id = create_part(ws.session, "Sim part")
    ws.configure(
        part_id,
        symbol_ref_external="Device:R",
        spice_datafile_id=datafile["id"],
        exclude_from_sim=False,
        sim_device="R",
        sim_pins="1=+ 2=-",
        sim_params="r=10k",
    )

    fields = ws.kicad.get(_part(part_id)).json()["fields"]
    assert fields["Sim.Device"]["value"] == "R"
    assert fields["Sim.Pins"]["value"] == "1=+ 2=-"
    assert fields["Sim.Params"]["value"] == "r=10k"
    assert fields["Sim.Library"]["value"] == kicad_refs.spice_path(datafile["name"])
    assert fields["Sim.Library"]["value"].startswith("${STOCKMGR_SPICE}/")


def test_sim_fields_suppressed_when_excluded_from_sim(ws: Tenant):
    """`exclude_from_sim` is how a user disables a model that is wrong or
    unfinished; shipping the fields anyway would override that."""
    datafile = _upload_spice(ws, "mypart")
    part_id = create_part(ws.session, "Sim part")
    ws.configure(
        part_id,
        symbol_ref_external="Device:R",
        spice_datafile_id=datafile["id"],
        exclude_from_sim=True,
        sim_device="R",
    )

    fields = ws.kicad.get(_part(part_id)).json()["fields"]
    assert not [key for key in fields if key.startswith("Sim.")]


# ---------------------------------------------------------------------
# Workspace isolation
# ---------------------------------------------------------------------


def test_token_sees_only_its_own_workspace(ws: Tenant, other: Tenant):
    mine = ws.category("Mine")
    other.category("Theirs")
    my_part = create_part(ws.session, "Mine", category_id=mine["id"])
    ws.configure(my_part, symbol_ref_external="Device:R")

    assert _names(ws.kicad.get(CATEGORIES).json()) == ["Mine"]
    assert _names(other.kicad.get(CATEGORIES).json()) == ["Theirs"]
    assert [row["id"] for row in ws.kicad.get(_parts_in(mine["id"])).json()] == [my_part]


def test_foreign_ids_are_404_not_403(ws: Tenant, other: Tenant):
    """Cross-workspace probes must be indistinguishable from nonexistent
    ids — 403 would confirm the id exists somewhere (ADR-0002)."""
    their_category = other.category("Theirs")
    their_part = create_part(other.session, "Theirs", category_id=their_category["id"])
    other.configure(their_part, symbol_ref_external="Device:R")

    assert ws.kicad.get(_parts_in(their_category["id"])).status_code == 404
    assert ws.kicad.get(_part(their_part)).status_code == 404


def test_token_pins_a_dual_member_to_one_workspace(ws: Tenant, other: Tenant):
    """The dangerous case for a PAT: its owner legitimately belongs to
    both tenants, so nothing about the *user* is out of bounds — only
    the credential is. Neither the `X-Workspace-Id` header nor the
    workspace cookie may move it (ADR-0029), and this surface has its
    own auth path, so the rule has to be pinned here too.
    """
    mine = ws.category("Mine")
    theirs = other.category("Theirs")
    my_part = create_part(ws.session, "Mine", category_id=mine["id"])
    ws.configure(my_part, symbol_ref_external="Device:R")
    their_part = create_part(other.session, "Theirs", category_id=theirs["id"])
    other.configure(their_part, symbol_ref_external="Device:R")

    # `other` invites `ws`'s user in, so they are a member of both.
    invite = other.session.post(
        "/api/invitations", json={"email": ws.email, "role": "member"}
    )
    assert invite.status_code in (200, 201), invite.text
    accepted = ws.session.post(
        "/api/invitations/accept", json={"token": invite.json()["data"]["token"]}
    )
    assert accepted.status_code == 200, accepted.text

    # Same token, now pointed at the other workspace every way a client can.
    ws.kicad.headers["X-Workspace-Id"] = str(other.workspace_id)
    ws.kicad.cookies.set("stockmgr_workspace", str(other.workspace_id))

    assert _names(ws.kicad.get(CATEGORIES).json()) == ["Mine"]
    assert ws.kicad.get(_parts_in(theirs["id"])).status_code == 404
    assert ws.kicad.get(_part(their_part)).status_code == 404
    assert [row["id"] for row in ws.kicad.get(_parts_in(mine["id"])).json()] == [my_part]


def test_uncategorized_bucket_is_workspace_scoped(ws: Tenant, other: Tenant):
    theirs = create_part(other.session, "Theirs")
    other.configure(theirs, symbol_ref_external="Device:R")

    assert ws.kicad.get(_parts_in("uncategorized")).json() == []
    assert _names(ws.kicad.get(CATEGORIES).json()) == []


# ---------------------------------------------------------------------
# Query budget
# ---------------------------------------------------------------------


class _QueryCounter:
    """Counts SQL statements issued during the block.

    Listens on the conftest `engine` fixture, NOT `infra.db.get_engine()`
    — the test session is bound to a connection from the former, and a
    listener on the latter silently counts nothing, which makes an
    equality assertion pass for the wrong reason.
    """

    def __init__(self, engine) -> None:
        self.count = 0
        self._engine = engine

    def __enter__(self):
        event.listen(self._engine, "before_cursor_execute", self._on_execute)
        return self

    def __exit__(self, *exc):
        event.remove(self._engine, "before_cursor_execute", self._on_execute)
        return False

    def _on_execute(self, conn, cursor, statement, parameters, context, executemany):
        self.count += 1


def _listing_query_count(
    ws: Tenant, engine, category_id: str, *, add: int, expect: int
) -> int:
    for index in range(add):
        part_id = create_part(ws.session, f"P{expect}-{index}", category_id=category_id)
        ws.configure(part_id, symbol_ref_external="Device:R")
    with _QueryCounter(engine) as counter:
        r = ws.kicad.get(_parts_in(category_id))
    assert r.status_code == 200, r.text
    assert len(r.json()) == expect
    assert counter.count > 0, "the counter saw nothing — it is on the wrong engine"
    return counter.count


def test_listing_does_not_scale_with_part_count(ws: Tenant, engine):
    """The symbol chooser hammers this endpoint, so an N+1 here
    multiplies by the size of the whole workspace.

    Comparing two sizes rather than asserting an exact count keeps the
    test from breaking on an unrelated extra lookup while still catching
    any per-row query.
    """
    category = ws.category("Passives")
    # One warm-up call: the first request on a fresh token also writes
    # and commits `last_used_at`, which the 300s throttle suppresses on
    # every later one.
    ws.kicad.get(_parts_in(category["id"]))

    small = _listing_query_count(ws, engine, category["id"], add=2, expect=2)
    large = _listing_query_count(ws, engine, category["id"], add=18, expect=20)
    # Ten times the parts. An N+1 would be +18 here; the tolerance of one
    # only absorbs savepoint bookkeeping, which is not part-count-driven.
    assert large <= small + 1, (
        f"listing issued {large} queries for 20 parts vs {small} for 2 — "
        "the resolution joins have regressed into an N+1"
    )


# ---------------------------------------------------------------------
# Client configuration — GET /api/eda/kicad-setup
# ---------------------------------------------------------------------


def test_kicad_setup_describes_a_usable_client_config(ws: Tenant):
    r = ws.session.get("/api/eda/kicad-setup")
    assert r.status_code == 200, r.text
    data = r.json()["data"]

    assert data["root_url"].endswith(kicad_library.API_PREFIX)
    assert data["categories_ttl"] == kicad_library.CATEGORIES_TTL_SECONDS
    assert data["parts_ttl"] == kicad_library.PARTS_TTL_SECONDS

    source = data["example"]["source"]
    assert source["type"] == "REST_API"
    assert source["api_version"] == "v1"
    assert source["root_url"] == data["root_url"]
    # The plaintext is unrecoverable server-side, so the example can only
    # ever carry a placeholder — the UI merges the real one in.
    assert source["token"] == kicad_library.TOKEN_PLACEHOLDER


def test_kicad_setup_meta_version_is_a_json_number(ws: Tenant):
    """`meta.version` is the one value in this payload that KiCad reads
    as a number. Quoting it — the natural thing to do given everything
    on `/kicad-api` is a string — makes the file unloadable."""
    r = ws.session.get("/api/eda/kicad-setup")
    version = r.json()["data"]["example"]["meta"]["version"]
    assert isinstance(version, float), f"{version!r} is {type(version).__name__}"
    assert '"version": "' not in r.text, "meta.version was serialised as a string"


def test_kicad_setup_root_url_is_where_the_library_actually_answers(ws: Tenant):
    """The advertised root_url and the mounted prefix must not drift."""
    root_url = ws.session.get("/api/eda/kicad-setup").json()["data"]["root_url"]
    path = root_url.split("://", 1)[-1].split("/", 1)[-1]
    assert ws.kicad.get(f"/{path}/v1/").status_code == 200


def test_kicad_setup_points_at_the_mcp_endpoint(ws: Tenant):
    """The settings page hands a freshly-minted token to three surfaces,
    and `/mcp` is the one that is not KiCad's. Pinned here because the
    URL is assembled from a constant `app/main.py` mounts and this
    endpoint only quotes — a moved mount that left this string behind
    would advertise a 404."""
    from app.mcp.server import MCP_PATH

    setup = ws.session.get("/api/eda/kicad-setup").json()["data"]
    assert setup["mcp_url"] == f"http://localhost:5173{MCP_PATH}"
    # Same origin as the library it sits beside — one deployment, and a
    # base URL that drifted between the two would be the bug.
    assert setup["mcp_url"].startswith(setup["root_url"].rsplit("/", 1)[0])
    assert "Bearer" in setup["mcp_note"]


def test_kicad_setup_omits_the_mcp_url_when_the_server_is_off(
    ws: Tenant, monkeypatch: pytest.MonkeyPatch
):
    """`MCP_ENABLED=false` unmounts the endpoint entirely (ADR-0030), so
    the setup page must stop naming it rather than print a path that
    404s."""
    from app.core.config import settings

    monkeypatch.setattr(settings(), "MCP_ENABLED", False)
    setup = ws.session.get("/api/eda/kicad-setup").json()["data"]
    assert setup["mcp_url"] is None


# ---------------------------------------------------------------------
# Value from the category template
# ---------------------------------------------------------------------


def _set_spec(ws: Tenant, part_id: str, key: str, value: str) -> None:
    """Write one canonical spec as a manual custom field.

    Through the HTTP API, unlike `_set_datasheet` — canonical spec keys
    are not provider-reserved, so this is the path a user takes.
    """
    r = ws.session.post(
        "/api/custom-fields",
        json={
            "object_type": "part",
            "object_id": part_id,
            "key": key,
            "value": value,
        },
    )
    assert r.status_code in (200, 201), r.text


def _resistor(ws: Tenant, category_id: str, **specs: str) -> str:
    part_id = create_part(ws.session, "RES SMD 10K OHM 1% 1/16W 0402", category_id=category_id)
    ws.configure(part_id, symbol_ref_external="Device:R")
    for key, value in specs.items():
        _set_spec(ws, part_id, key, value)
    return part_id


def test_value_is_rendered_from_the_category_template(ws: Tenant):
    """The whole point: an imported part is named after the provider's
    marketing copy, and the schematic has to show `10 kΩ 1% 0603`."""
    category = ws.category(
        "Resistors", value_template="{resistance} {tolerance} {package}"
    )
    part_id = _resistor(
        ws, category["id"], resistance="10 kΩ", tolerance="1%", package="0603"
    )

    document = ws.kicad.get(_part(part_id)).json()
    assert document["fields"]["value"] == {"value": "10 kΩ 1% 0603"}
    # `name` is untouched — the template drives the symbol, not the row.
    assert document["name"] == "RES SMD 10K OHM 1% 1/16W 0402"


def test_an_explicit_part_value_still_outranks_the_template(ws: Tenant):
    category = ws.category("Resistors", value_template="{resistance} {package}")
    part_id = _resistor(ws, category["id"], resistance="10 kΩ", package="0603")
    ws.configure(part_id, symbol_ref_external="Device:R", value="R10K")

    assert ws.kicad.get(_part(part_id)).json()["fields"]["value"] == {"value": "R10K"}


def test_the_part_name_is_the_last_resort(ws: Tenant):
    """A template whose placeholders all resolve to nothing must not
    leave the symbol with an empty Value."""
    category = ws.category("Resistors", value_template="{resistance} {package}")
    part_id = create_part(ws.session, "Mystery", category_id=category["id"])
    ws.configure(part_id, symbol_ref_external="Device:R")

    assert ws.kicad.get(_part(part_id)).json()["fields"]["value"] == {"value": "Mystery"}


def test_a_category_with_no_template_is_unchanged(ws: Tenant):
    category = ws.category("Resistors")
    part_id = _resistor(ws, category["id"], resistance="10 kΩ")
    assert ws.kicad.get(_part(part_id)).json()["fields"]["value"] == {
        "value": "RES SMD 10K OHM 1% 1/16W 0402"
    }


def test_mpn_is_the_default_template_for_everything_else(ws: Tenant):
    category = ws.category("Microcontrollers", value_template="{mpn}")
    mpn = f"STM32-{uuid.uuid4().hex[:6]}"
    part_id = create_part(
        ws.session, "MCU 32BIT ARM 256KB", mpn=mpn, category_id=category["id"]
    )
    ws.configure(part_id, symbol_ref_external="Device:R")

    assert ws.kicad.get(_part(part_id)).json()["fields"]["value"] == {"value": mpn}


def test_kicad_fields_are_emitted_as_hidden_symbol_fields(ws: Tenant):
    category = ws.category(
        "Resistors", kicad_fields=["resistance", "tolerance", "voltage_rating"]
    )
    part_id = _resistor(ws, category["id"], resistance="10 kΩ", tolerance="1%")

    fields = ws.kicad.get(_part(part_id)).json()["fields"]
    assert fields["Resistance"] == {"value": "10 kΩ", "visible": "False"}
    assert fields["Tolerance"] == {"value": "1%", "visible": "False"}
    # A listed key the part has no spec for is omitted, not blanked.
    assert "Voltage Rating" not in fields


def test_a_spec_field_cannot_shadow_a_built_in_one(ws: Tenant):
    """KiCad matches its mandatory fields case-insensitively, so a spec
    key called `value` would give the symbol two Values."""
    category = ws.category("Resistors", kicad_fields=["value", "description", "package"])
    part_id = create_part(
        ws.session, "R10K", description="ten k", category_id=category["id"]
    )
    ws.configure(part_id, symbol_ref_external="Device:R")
    for key in ("value", "description"):
        _set_spec(ws, part_id, key, "SHADOW")
    _set_spec(ws, part_id, "package", "0603")

    fields = ws.kicad.get(_part(part_id)).json()["fields"]
    assert fields["value"] == {"value": "R10K"}
    assert fields["description"]["value"] == "ten k"
    assert "Value" not in fields
    assert "Description" not in fields
    # The key that collides with nothing is still emitted.
    assert fields["Package"]["value"] == "0603"


def test_the_rendered_value_joins_the_keywords(ws: Tenant):
    """The symbol chooser searches keywords. A part named after the
    provider description is otherwise unfindable by what it is."""
    category = ws.category("Resistors", value_template="{resistance} {package}")
    part_id = _resistor(ws, category["id"], resistance="10 kΩ", package="0603")
    ws.configure(part_id, symbol_ref_external="Device:R", keywords="resistor smd")

    document = ws.kicad.get(_part(part_id)).json()
    assert document["keywords"] == "resistor smd 10 kΩ 0603"
    assert document["fields"]["keywords"]["value"] == document["keywords"]


def test_keywords_are_untouched_when_nothing_renders(ws: Tenant):
    category = ws.category("Resistors")
    part_id = _resistor(ws, category["id"])
    ws.configure(part_id, symbol_ref_external="Device:R", keywords="resistor")

    assert ws.kicad.get(_part(part_id)).json()["keywords"] == "resistor"


def test_the_template_is_inherited_from_an_ancestor_category(ws: Tenant):
    """A template set on *Capacitors* covers *Capacitors / Ceramic*
    without being repeated on every leaf."""
    parent = ws.category(
        "Capacitors",
        value_template="{capacitance} {voltage_rating}",
        kicad_fields=["capacitance"],
    )
    child = ws.category("Ceramic", parent_id=parent["id"])
    part_id = create_part(ws.session, "CAP CER 1UF 50V X7R", category_id=child["id"])
    ws.configure(part_id, symbol_ref_external="Device:C")
    _set_spec(ws, part_id, "capacitance", "1 µF")
    _set_spec(ws, part_id, "voltage_rating", "50 V")

    fields = ws.kicad.get(_part(part_id)).json()["fields"]
    assert fields["value"] == {"value": "1 µF 50 V"}
    assert fields["Capacitance"]["value"] == "1 µF"


def test_a_childs_own_template_wins_over_its_parents(ws: Tenant):
    parent = ws.category("Capacitors", value_template="{capacitance}")
    child = ws.category(
        "Electrolytic", parent_id=parent["id"], value_template="{capacitance} elyt"
    )
    part_id = create_part(ws.session, "CAP", category_id=child["id"])
    ws.configure(part_id, symbol_ref_external="Device:C")
    _set_spec(ws, part_id, "capacitance", "1000 µF")

    assert ws.kicad.get(_part(part_id)).json()["fields"]["value"] == {
        "value": "1000 µF elyt"
    }


def test_template_and_fields_inherit_independently(ws: Tenant):
    """A child may override the template while still taking its
    parent's field list, and vice versa."""
    parent = ws.category(
        "Capacitors", value_template="{capacitance}", kicad_fields=["dielectric"]
    )
    child = ws.category(
        "Ceramic", parent_id=parent["id"], value_template="C {capacitance}"
    )
    part_id = create_part(ws.session, "CAP", category_id=child["id"])
    ws.configure(part_id, symbol_ref_external="Device:C")
    _set_spec(ws, part_id, "capacitance", "1 µF")
    _set_spec(ws, part_id, "dielectric", "X7R")

    fields = ws.kicad.get(_part(part_id)).json()["fields"]
    assert fields["value"] == {"value": "C 1 µF"}
    assert fields["Dielectric"]["value"] == "X7R"


def test_an_empty_field_list_stops_the_walk(ws: Tenant):
    """Null inherits; `[]` is an explicit "emit nothing here"."""
    parent = ws.category("Capacitors", kicad_fields=["dielectric"])
    child = ws.category("Ceramic", parent_id=parent["id"], kicad_fields=[])
    part_id = create_part(ws.session, "CAP", category_id=child["id"])
    ws.configure(part_id, symbol_ref_external="Device:C")
    _set_spec(ws, part_id, "dielectric", "X7R")

    assert "Dielectric" not in ws.kicad.get(_part(part_id)).json()["fields"]


def test_an_archived_ancestor_stops_the_walk(ws: Tenant):
    """Archiving a category promotes its direct children to root, and
    the walk only reads ACTIVE rows — so a template stops applying to
    the subtree the moment the category carrying it is archived."""
    parent = ws.category("Capacitors", value_template="{capacitance}")
    child = ws.category("Ceramic", parent_id=parent["id"])
    part_id = create_part(ws.session, "CAP", category_id=child["id"])
    ws.configure(part_id, symbol_ref_external="Device:C")
    _set_spec(ws, part_id, "capacitance", "1 µF")
    assert ws.kicad.get(_part(part_id)).json()["fields"]["value"] == {"value": "1 µF"}

    assert ws.session.post(f"/api/categories/{parent['id']}/archive").status_code == 200
    assert ws.kicad.get(_part(part_id)).json()["fields"]["value"] == {"value": "CAP"}


def test_specs_do_not_leak_across_workspaces(ws: Tenant, other: Tenant):
    """The spec join is workspace-filtered like every other read here."""
    category = ws.category("Resistors", value_template="{resistance}")
    part_id = _resistor(ws, category["id"], resistance="10 kΩ")

    assert ws.kicad.get(_part(part_id)).json()["fields"]["value"]["value"] == "10 kΩ"
    assert other.kicad.get(_part(part_id)).status_code == 404


def test_the_listing_renders_the_same_value_as_the_detail(ws: Tenant):
    category = ws.category(
        "Resistors",
        value_template="{resistance} {package}",
        kicad_fields=["resistance"],
    )
    part_id = _resistor(ws, category["id"], resistance="10 kΩ", package="0603")

    listed = ws.kicad.get(_parts_in(category["id"])).json()[0]
    assert listed == ws.kicad.get(_part(part_id)).json()


def test_templated_listing_does_not_scale_with_part_count(ws: Tenant, engine):
    """The specs join replaced the datasheet join; a template that reads
    four keys must not turn it back into a per-row lookup."""
    category = ws.category(
        "Passives",
        value_template="{resistance} {tolerance} {package}",
        kicad_fields=["resistance", "tolerance", "package", "power"],
    )
    ws.kicad.get(_parts_in(category["id"]))

    small = _listing_query_count(ws, engine, category["id"], add=2, expect=2)
    large = _listing_query_count(ws, engine, category["id"], add=18, expect=20)
    assert large <= small + 1, (
        f"listing issued {large} queries for 20 parts vs {small} for 2 — "
        "the spec join has regressed into an N+1"
    )


def test_an_inherited_template_does_not_add_a_query_per_part(ws: Tenant, engine):
    """Walking `parent_id` costs one lookup for the page, not one per
    row — the whole reason the ancestors are loaded in a batch."""
    parent = ws.category("Passives", value_template="{resistance} {package}")
    category = ws.category("Resistors", parent_id=parent["id"])
    ws.kicad.get(_parts_in(category["id"]))

    small = _listing_query_count(ws, engine, category["id"], add=2, expect=2)
    large = _listing_query_count(ws, engine, category["id"], add=18, expect=20)
    assert large <= small + 1


# ---------------------------------------------------------------------
# Category tree names (B5)
# ---------------------------------------------------------------------


def test_a_subcategory_is_named_by_its_full_path(ws: Tenant):
    """The httplib category document is `{id,name,description}` — there
    is nowhere else to convey nesting, so the name carries it."""
    parent = ws.category("Capacitors")
    child = ws.category("Ceramic", parent_id=parent["id"])
    grandchild = ws.category("X7R", parent_id=child["id"])

    rows = {row["id"]: row["name"] for row in ws.kicad.get(CATEGORIES).json()}
    assert rows[parent["id"]] == "Capacitors"
    assert rows[child["id"]] == "Capacitors / Ceramic"
    assert rows[grandchild["id"]] == "Capacitors / Ceramic / X7R"


def test_the_tree_is_listed_parent_first(ws: Tenant):
    """Depth-first: a child sits directly under its parent, and siblings
    are ordered by `(sort_order, name)` within it."""
    caps = ws.category("Capacitors", sort_order=1)
    ws.category("Tantalum", parent_id=caps["id"], sort_order=2)
    ws.category("Ceramic", parent_id=caps["id"], sort_order=1)
    diodes = ws.category("Diodes", sort_order=2)
    ws.category("Zener", parent_id=diodes["id"])

    assert _names(ws.kicad.get(CATEGORIES).json()) == [
        "Capacitors",
        "Capacitors / Ceramic",
        "Capacitors / Tantalum",
        "Diodes",
        "Diodes / Zener",
    ]


def test_a_child_of_an_archived_parent_lists_as_a_root(ws: Tenant):
    """An archived category is absent from this document, so nothing may
    be named through it. `archive_category` promotes direct children to
    root, and the path builder treats any parent it cannot see the same
    way — belt and braces, since only raw SQL can produce the second."""
    parent = ws.category("Capacitors")
    child = ws.category("Ceramic", parent_id=parent["id"])
    assert ws.session.post(f"/api/categories/{parent['id']}/archive").status_code == 200

    rows = ws.kicad.get(CATEGORIES).json()
    assert _names(rows) == ["Ceramic"]
    assert rows[0]["id"] == child["id"]
