"""`/kicad-api/pcm` — the repository KiCad's add-on manager installs from.

What has to hold, and why each of these breaks a real install if it
drifts:

* **Read-only tokens, and nothing else.** The PCM sends no headers, so
  the credential rides the URL path. A full-parity token there is the
  leak `read_only` was minted to prevent, so it is refused — with the
  same 404 as a revoked one, because telling the two apart would tell an
  attacker which stolen token to keep.
* **The layout is KiCad's, not ours.** `symbols/`, `footprints/*.pretty/`,
  `3dmodels/` and `resources/` are the only folders the extractor
  recognises, and it rewrites each to `<3rd-party>/<folder>/<id>/…`. A
  `(model …)` path that doesn't name that exact location resolves to
  nothing on the installed machine, which is a missing 3D model on every
  board that places the footprint.
* **The version has to grow.** It is derived from timestamps rather than
  stored, so anything that changes content and not `updated_at` leaves
  installed copies stranded on an old package forever.
* **The documents have to validate.** `packages.json` publishes the
  archive's digest and `repository.json` publishes `packages.json`'s;
  KiCad checks both, and its schema pins the shape of every field.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import pathlib
import re
import time
import uuid
import zipfile
from datetime import datetime, timedelta, timezone

import jsonschema
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

import app.core.ratelimit as _ratelimit_mod
from app.core.time import utcnow
from app.domain.eda import kicad_refs, pcm, sexpr
from app.domain.eda.models import EdaSymbol
from app.main import app
from tests._factories import signup_user


def _repository(token: str) -> str:
    return f"/kicad-api/pcm/{token}/repository.json"


def _packages(token: str) -> str:
    return f"/kicad-api/pcm/{token}/packages.json"


def _archive(token: str) -> str:
    return f"/kicad-api/pcm/{token}/package.zip"


def _all_paths(token: str) -> list[str]:
    return [_repository(token), _packages(token), _archive(token)]


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


def _footprint_text(name: str, *, model: str | None = None) -> str:
    node = (
        f'  (model "{model}" (offset (xyz 0 0 0)) (scale (xyz 1 1 1)))\n'
        if model
        else ""
    )
    return (
        f'(footprint "{name}" (layer "F.Cu")\n'
        f'  (descr "test")\n'
        f'  (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu"))\n'
        f"{node})\n"
    )


_STEP_BYTES = b"ISO-10303-21;\nHEADER;\nENDSEC;\nEND-ISO-10303-21;\n"
_SPICE_BYTES = b".SUBCKT TEST 1 2\n.ENDS\n"


class Tenant:
    """One signed-up workspace, its browser session and its PCM client.

    `pcm` carries no credential at all — the PCM sends none, and every
    request it makes names the token in the URL instead.
    """

    def __init__(self) -> None:
        self.session = TestClient(app)
        signed_up = signup_user(self.session, email=f"u-{uuid.uuid4().hex[:8]}@example.com")
        self.workspace_id = uuid.UUID(signed_up.json()["data"]["workspace_id"])
        self.token = self.mint()
        self.pcm = TestClient(app)

    def mint(self, *, read_only: bool = True, expires_in_days: int | None = None) -> str:
        body: dict[str, object] = {
            "label": f"pcm {uuid.uuid4().hex[:6]}",
            "read_only": read_only,
        }
        if expires_in_days is not None:
            body["expires_in_days"] = expires_in_days
        r = self.session.post("/api/tokens", json=body)
        assert r.status_code == 201, r.text
        return r.json()["data"]["token"]

    # -- fixture builders, all through the HTTP API per house rules --

    def category(self, name: str, **extra) -> dict:
        r = self.session.post("/api/categories", json={"name": name, **extra})
        assert r.status_code in (200, 201), r.text
        return r.json()["data"]

    def symbol(self, entry: str, *, category_id: str | None = None) -> dict:
        return self._upload("symbols", f"{entry}.kicad_sym", _symbol_text(entry), category_id)

    def footprint(
        self, entry: str, *, category_id: str | None = None, model: str | None = None
    ) -> dict:
        return self._upload(
            "footprints",
            f"{entry}.kicad_mod",
            _footprint_text(entry, model=model),
            category_id,
        )

    def _upload(self, kind: str, filename: str, text: str, category_id: str | None) -> dict:
        r = self.session.post(
            f"/api/eda/{kind}",
            files={"file": (filename, text.encode(), "application/octet-stream")},
            data={"category_id": category_id} if category_id else {},
        )
        assert r.status_code in (200, 201), r.text
        return r.json()["data"]

    def datafile(self, filename: str, raw: bytes, *, name: str | None = None) -> dict:
        r = self.session.post(
            "/api/eda/datafiles",
            files={"file": (filename, raw, "application/octet-stream")},
            data={"name": name} if name else {},
        )
        assert r.status_code in (200, 201), r.text
        return r.json()["data"]

    # -- reads --

    def zip_members(self) -> list[str]:
        return sorted(_open_zip(self.pcm.get(_archive(self.token))).namelist())

    def packages(self) -> dict:
        r = self.pcm.get(_packages(self.token))
        assert r.status_code == 200, r.text
        return r.json()

    def version(self) -> str:
        packages = self.packages()["packages"]
        return packages[0]["versions"][0]["version"] if packages else ""


def _open_zip(response) -> zipfile.ZipFile:
    assert response.status_code == 200, response.text
    return zipfile.ZipFile(io.BytesIO(response.content))


def _stocked(tenant: Tenant) -> Tenant:
    """A workspace with one of everything the package can carry."""
    passives = tenant.category("Passives")["id"]
    tenant.symbol("R_Generic", category_id=passives)
    tenant.datafile("cube.step", _STEP_BYTES, name="cube.step")
    tenant.datafile("diode.lib", _SPICE_BYTES, name="diode.lib")
    tenant.footprint(
        "R_0402",
        category_id=passives,
        model=kicad_refs.model_path("cube.step"),
    )
    return tenant


@pytest.fixture
def ws(db) -> Tenant:
    return _stocked(Tenant())


@pytest.fixture
def bare(db) -> Tenant:
    """A workspace with no library content at all."""
    return Tenant()


@pytest.fixture
def other(db) -> Tenant:
    return Tenant()


# ---------------------------------------------------------------------
# Authentication — read-only or nothing, and every refusal is one 404
# ---------------------------------------------------------------------


@pytest.mark.parametrize("path", ["repository.json", "packages.json", "package.zip"])
def test_a_read_only_token_is_served(ws: Tenant, path: str):
    assert ws.pcm.get(f"/kicad-api/pcm/{ws.token}/{path}").status_code == 200


@pytest.mark.parametrize("path", ["repository.json", "packages.json", "package.zip"])
def test_a_full_parity_token_is_404(db, path: str):
    """The whole point of the surface's read-only rule.

    A token that can write, pasted into a URL that ends up in proxy logs
    and browser history, is the exposure ADR-0029 minted the `read_only`
    flag to bound. It is refused as though it were invalid — a distinct
    error would tell whoever found the URL that the credential was real
    and worth trying elsewhere.
    """
    tenant = Tenant()
    full = tenant.mint(read_only=False)
    assert tenant.pcm.get(f"/kicad-api/pcm/{full}/{path}").status_code == 404


@pytest.mark.parametrize(
    "token",
    [
        "garbage",
        "smk_deadbeef.nope",
        f"smk_{uuid.uuid4().hex}.wrong",
        "",
    ],
    ids=["garbage", "malformed-id", "unknown-id", "empty"],
)
def test_unusable_tokens_are_404(db, token: str):
    # An empty token collapses the path to `//repository.json`, which is
    # simply an unrouted URL — still a 404, which is the point.
    assert TestClient(app).get(_repository(token)).status_code == 404


def test_a_revoked_token_is_404(ws: Tenant):
    listed = ws.session.get("/api/tokens").json()["data"]
    revoked = ws.session.post(f"/api/tokens/{listed[0]['id']}/revoke")
    assert revoked.status_code == 200, revoked.text
    assert ws.pcm.get(_repository(ws.token)).status_code == 404


def test_an_expired_token_is_404(db):
    tenant = Tenant()
    token = tenant.mint(expires_in_days=1)
    assert tenant.pcm.get(_repository(token)).status_code == 200

    from app.domain.tokens.models import ApiToken

    db.execute(
        update(ApiToken)
        .where(ApiToken.workspace_id == tenant.workspace_id)
        .values(expires_at=utcnow() - timedelta(minutes=1))
    )
    db.flush()
    assert tenant.pcm.get(_repository(token)).status_code == 404


def test_a_session_cookie_does_not_authenticate(ws: Tenant):
    """One surface, one credential. The browser's ambient cookie must not
    open an envelope-free, CSRF-exempt endpoint."""
    assert ws.session.get(_repository("garbage")).status_code == 404


def test_the_error_body_never_echoes_the_token(db):
    """A 404 body is the one place a live credential could be reflected
    back — into a log aggregator, a proxy error page, a screenshot."""
    token = f"smk_{uuid.uuid4().hex}.secretsecretsecret"
    response = TestClient(app).get(_repository(token))
    assert response.status_code == 404
    assert "secretsecretsecret" not in response.text
    assert token not in response.text


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "/kicad-api/pcm/smk_abc.def/packages.json",
            "/kicad-api/pcm/<token>/packages.json",
        ),
        ("/kicad-api/pcm/smk_abc.def", "/kicad-api/pcm/<token>"),
        # The catalog has the identical exposure: a bearer token in a URL
        # a human pastes around.
        ("/catalog/sometoken/index.json", "/catalog/<token>/index.json"),
        # Absolute URLs, because Sentry reports one rather than a path.
        (
            "https://parts.example.com/kicad-api/pcm/smk_abc.def/package.zip",
            "https://parts.example.com/kicad-api/pcm/<token>/package.zip",
        ),
        # Left alone: the sibling KiCad surface authenticates by header,
        # and a `/catalog/` deeper in some other path is an id.
        ("/kicad-api/v1/categories.json", "/kicad-api/v1/categories.json"),
        ("/api/parts/catalog/abc", "/api/parts/catalog/abc"),
        ("https://parts.example.com", "https://parts.example.com"),
    ],
)
def test_mask_credential_segment(raw: str, expected: str):
    from app.core.responses import mask_credential_segment

    assert mask_credential_segment(raw) == expected


def _archive_body(tenant: Tenant) -> str:
    """Member names AND every inflated byte of the archive, as one string.

    Reading `namelist()` alone is not an isolation test. Symbols are
    MERGED into `symbols/SM_<slug>.kicad_sym`, so a leaked symbol's entry
    name appears nowhere in the member list — a deliberately-leaking build
    passed a namelist-only version of this test on all three assertions.
    Whatever leaks has to land in the bytes, so the bytes are what's
    searched.
    """
    archive = _open_zip(tenant.pcm.get(_archive(tenant.token)))
    parts = list(archive.namelist())
    parts.extend(
        archive.read(name).decode("utf-8", "replace") for name in archive.namelist()
    )
    return "\n".join(parts)


def test_one_workspaces_token_cannot_see_anothers_library(ws: Tenant, other: Tenant):
    """One decoy per content type, each filed under a category.

    The category matters: an uncategorised decoy would file under
    `SM_uncategorized`, a stem the victim's own archive already has, so
    asserting on the stem would prove nothing about whose rows built it.
    """
    connectors = other.category("Connectors")["id"]
    other.symbol("CONN_SECRET", category_id=connectors)
    other.footprint("FP_SECRET", category_id=connectors)
    other.datafile("SECRET_HOUSING.step", _STEP_BYTES, name="SECRET_HOUSING.step")

    body = _archive_body(ws)
    assert "R_Generic" in body, "the workspace's own symbol should be here"
    for leak in ("CONN_SECRET", "FP_SECRET", "SECRET_HOUSING", "SM_connectors"):
        assert leak not in body, f"{leak} leaked from another workspace"

    # The documents are built from the same plan, so they get the same
    # treatment rather than being assumed clean.
    assert "SECRET" not in json.dumps(ws.packages())


def test_the_package_identifier_is_the_workspace(ws: Tenant):
    package = ws.packages()["packages"][0]
    assert package["identifier"] == f"com.stockmanager.{ws.workspace_id.hex}"


# ---------------------------------------------------------------------
# Archive layout
# ---------------------------------------------------------------------


def _symbol_names(tenant: Tenant, stem: str = "SM_passives") -> list[str]:
    archive = _open_zip(tenant.pcm.get(_archive(tenant.token)))
    text = archive.read(f"symbols/{stem}.kicad_sym").decode()
    return [name for name, _ in sexpr.entries(text)]


def test_member_layout_is_exactly_what_the_pcm_extracts(ws: Tenant):
    """Every top-level folder here is in KiCad's PCM_PACKAGE_DIRECTORIES;
    a member under anything else is silently dropped on install."""
    assert ws.zip_members() == [
        "3dmodels/cube.step",
        "footprints/SM_passives.pretty/R_0402.kicad_mod",
        "metadata.json",
        "resources/spice/diode.lib",
        "symbols/SM_passives.kicad_sym",
    ]


def test_symbol_libraries_parse_and_carry_their_entries(ws: Tenant):
    """The library file is a concatenation of stored canonical entries,
    so nothing re-parses it on the way out — which is exactly why the
    test has to."""
    archive = _open_zip(ws.pcm.get(_archive(ws.token)))
    text = archive.read("symbols/SM_passives.kicad_sym").decode()

    root = sexpr.parse(text)
    assert sexpr.head(root) == sexpr.SYMBOL_LIB_ROOT
    assert [name for name, _ in sexpr.entries(text)] == ["R_Generic"]


def test_several_symbols_share_one_library_file(ws: Tenant):
    passives = ws.session.get("/api/categories").json()["data"][0]["id"]
    ws.symbol("C_Generic", category_id=passives)
    assert _symbol_names(ws) == ["C_Generic", "R_Generic"]


def test_model_paths_are_rewritten_to_the_install_location(ws: Tenant):
    """The one rewrite the build performs.

    KiCad's extractor puts `3dmodels/cube.step` at
    `<3rd-party>/3dmodels/<identifier-with-underscores>/cube.step`, so
    that is the only string a `(model …)` node can carry and still
    resolve. The stored `${STOCKMGR_3D}` form must be gone entirely.
    """
    archive = _open_zip(ws.pcm.get(_archive(ws.token)))
    text = archive.read("footprints/SM_passives.pretty/R_0402.kicad_mod").decode()

    expected = (
        f"${{KICAD8_3RD_PARTY}}/3dmodels/com_stockmanager_{ws.workspace_id.hex}/cube.step"
    )
    assert sexpr.model_paths(sexpr.parse(text)) == [expected]
    assert kicad_refs.MODEL_PATH_VAR not in text
    # The rewrite must not disturb how the model is placed on the board.
    assert "(offset" in text and "(scale" in text


def test_foreign_model_paths_are_left_alone(ws: Tenant):
    """A hand-uploaded footprint may reference a library the user already
    has; rewriting that would break it."""
    foreign = "${KICAD8_3DMODEL_DIR}/Resistor_SMD.3dshapes/R_0603.step"
    ws.footprint("R_0603", model=foreign)
    archive = _open_zip(ws.pcm.get(_archive(ws.token)))
    text = archive.read("footprints/SM_uncategorized.pretty/R_0603.kicad_mod").decode()
    assert sexpr.model_paths(sexpr.parse(text)) == [foreign]


def test_spice_models_ship_under_resources(ws: Tenant):
    """The PCM has no SPICE slot, so they go in `resources/` and the user
    points one path variable at the installed directory. The bytes are
    never rewritten — the simulator is the arbiter of what's in them."""
    archive = _open_zip(ws.pcm.get(_archive(ws.token)))
    assert archive.read("resources/spice/diode.lib") == _SPICE_BYTES


def test_kicad_setup_names_the_installed_spice_directory(ws: Tenant):
    setup = ws.session.get("/api/eda/kicad-setup").json()["data"]
    assert setup["pcm_spice_path_variable"] == "STOCKMGR_SPICE"
    assert setup["pcm_spice_path_value"] == (
        f"${{KICAD8_3RD_PARTY}}/resources/com_stockmanager_{ws.workspace_id.hex}/spice"
    )
    assert setup["pcm_repository_url_template"] == (
        "http://localhost:5173/kicad-api/pcm/PASTE_YOUR_READONLY_TOKEN/repository.json"
    )
    assert "read-only" in setup["read_only_note"]


def test_archived_entries_do_not_ship(ws: Tenant):
    symbol = ws.symbol("R_Doomed")
    assert "R_Doomed" in _symbol_names(ws, "SM_uncategorized")

    assert ws.session.post(f"/api/eda/symbols/{symbol['id']}/archive").status_code == 200
    # It was the only uncategorized entry, so the library file goes with
    # it rather than shipping empty.
    assert "symbols/SM_uncategorized.kicad_sym" not in ws.zip_members()


def test_an_entry_under_an_archived_category_files_as_uncategorized(ws: Tenant):
    """Phase 5 already told KiCad this entry lives in
    `PCM_SM_uncategorized`; the file has to be there or every part using
    it is a broken symbol."""
    doomed = ws.category("Doomed")
    ws.symbol("R_Orphan", category_id=doomed["id"])
    assert ws.session.post(f"/api/categories/{doomed['id']}/archive").status_code == 200

    members = ws.zip_members()
    assert "symbols/SM_doomed.kicad_sym" not in members
    assert "R_Orphan" in _symbol_names(ws, "SM_uncategorized")


def test_an_empty_workspace_publishes_no_packages(bare: Tenant):
    """Valid repository, nothing to install. A package that installed to
    nothing would register empty libraries on the user's machine."""
    assert bare.packages() == {"packages": []}
    # The archive still answers, so nothing here is a state oracle.
    assert bare.zip_members() == ["metadata.json"]


def test_entry_names_that_are_not_filenames_are_skipped(ws: Tenant):
    """`eda_*.name` is an unconstrained String(200) fed by parsed file
    content, so a name can contain a path separator. Emitting one
    verbatim would put a traversing member in an archive we hand to a
    desktop application."""
    ws.symbol("../../escape")
    ws.symbol("R_Fine")

    members = ws.zip_members()
    assert not any(".." in member for member in members)
    assert "R_Fine" in _symbol_names(ws, "SM_uncategorized")
    assert "../../escape" not in _symbol_names(ws, "SM_uncategorized")


def test_a_step_and_a_wrl_sharing_a_name_do_not_collide(ws: Tenant, caplog):
    """Uniqueness on `eda_datafiles` is (workspace, KIND, name), so this
    pair is legal — but both want `3dmodels/cube`, and a zip with two
    members at one path extracts unpredictably. STEP wins, because that
    is the format preferred wherever both exist and the `(model …)` path
    names the row's `name` with no way to say which kind it meant.

    Folding the kind into the member path would "fix" the collision by
    breaking the `${STOCKMGR_3D}/<name>` ↔ member mapping that the
    phase-3 rewrites depend on, so the loser is dropped with a note
    instead.
    """
    ws.datafile("a.step", _STEP_BYTES, name="cube")
    ws.datafile("a.wrl", b"#VRML V2.0 utf8\n", name="cube")

    with caplog.at_level(logging.WARNING, logger="app.domain.eda.pcm"):
        response = ws.pcm.get(_archive(ws.token))
    archive = _open_zip(response)

    members = archive.namelist()
    assert members.count("3dmodels/cube") == 1
    assert archive.read("3dmodels/cube") == _STEP_BYTES

    notes = [r.getMessage() for r in caplog.records if "claimed by more than one" in r.getMessage()]
    assert len(notes) == 1, "the dropped file should leave exactly one note"
    assert "shipping the step" in notes[0] and "dropping the wrl" in notes[0]

    # The dropped member must not be counted twice in what the PCM
    # preallocates for the install.
    version = ws.packages()["packages"][0]["versions"][0]
    assert version["install_size"] == sum(
        info.file_size for info in archive.infolist()
    )


def test_a_model_path_that_is_not_a_filename_is_dropped(ws: Tenant):
    """The remainder after `${STOCKMGR_3D}/` is never a zip member, so
    the archive-member guard never sees it — but it IS interpolated into
    an absolute path KiCad resolves on the user's machine, where a `..`
    walks out of the installed package."""
    ws.footprint("R_Escape", model=f"{kicad_refs.MODEL_PATH_VAR}/../../../etc/passwd")

    archive = _open_zip(ws.pcm.get(_archive(ws.token)))
    text = archive.read("footprints/SM_uncategorized.pretty/R_Escape.kicad_mod").decode()
    assert sexpr.model_paths(sexpr.parse(text)) == []
    assert ".." not in text


def test_metadata_inside_the_archive_omits_the_download_fields(ws: Tenant):
    """They describe the archive and so cannot live inside it — the PCM
    docs say they belong only to the repository's copy."""
    archive = _open_zip(ws.pcm.get(_archive(ws.token)))
    metadata = json.loads(archive.read("metadata.json"))
    version = metadata["versions"][0]
    assert set(version) == {"version", "status", "kicad_version"}
    assert metadata["identifier"] == f"com.stockmanager.{ws.workspace_id.hex}"
    assert metadata["type"] == "library"


# ---------------------------------------------------------------------
# Determinism and versioning
# ---------------------------------------------------------------------


def test_rebuilding_unchanged_content_is_byte_identical(ws: Tenant):
    """`download_sha256` is published in `packages.json` and checked by
    the PCM, so the same content has to deflate to the same bytes — zip
    member order, timestamps and the build host's platform all included."""
    first = ws.pcm.get(_archive(ws.token))
    second = ws.pcm.get(_archive(ws.token))
    assert first.content == second.content
    assert ws.packages()["packages"][0]["versions"][0]["download_sha256"] == (
        hashlib.sha256(first.content).hexdigest()
    )


def test_the_cache_keeps_one_zip_and_sweeps_abandoned_scratch_files(
    ws: Tenant, monkeypatch
):
    """The fingerprint changes on every content change, so without a
    sweep a busy workspace leaves one stale zip per edit — and a build
    killed between `mkstemp` and `os.replace` leaves a `.tmp` that
    nothing else will ever remove."""
    ws.pcm.get(_archive(ws.token))
    cache_dir = pathlib.Path(pcm._cache_dir(ws.workspace_id))
    assert len(list(cache_dir.glob("*.zip"))) == 1

    stale = cache_dir / "pcm.abandoned.tmp"
    stale.write_bytes(b"half a zip")
    fresh = cache_dir / "pcm.inflight.tmp"
    fresh.write_bytes(b"a build happening right now")

    # Age past the grace period without waiting it out.
    monkeypatch.setattr(
        pcm, "_now_seconds", lambda: time.time() + pcm._TMP_GRACE_SECONDS + 60
    )
    ws.symbol("R_Next")
    ws.pcm.get(_archive(ws.token))

    assert len(list(cache_dir.glob("*.zip"))) == 1, "superseded zips should be gone"
    assert not stale.exists()
    # `fresh` is also past the (moved) cutoff — what this pins is that a
    # file written *within* the grace period survives, so a concurrent
    # build is never robbed of its scratch file.
    fresh.write_bytes(b"written after the clock moved")
    monkeypatch.undo()
    ws.symbol("R_Later2")
    ws.pcm.get(_archive(ws.token))
    assert fresh.exists(), "an in-flight scratch file must not be swept"
    fresh.unlink()


def test_a_cache_hit_streams_from_disk_instead_of_buffering(db, ws: Tenant):
    """The package is capped at 200 MiB of content and this route allows
    30 requests a minute, so a hit that read the archive into the worker
    would put that multiple in resident memory for nothing."""
    from app.domain.workspaces.models import Workspace

    workspace = db.get(Workspace, ws.workspace_id)
    first = pcm.build_package(db, ws=workspace)
    assert first.serve_from_disk and first.data is None

    second = pcm.build_package(db, ws=workspace)
    assert second.serve_from_disk and second.data is None
    assert second.sha256 == first.sha256
    assert second.install_size == first.install_size

    # And the route still returns the same bytes it did before.
    assert (
        hashlib.sha256(ws.pcm.get(_archive(ws.token)).content).hexdigest()
        == first.sha256
    )


def test_a_cache_hit_does_not_open_the_archive(db, ws: Tenant, monkeypatch):
    """The sidecar exists so `repository.json` and `packages.json` — which
    need only the digest and the two sizes — are answered without touching
    the zip at all."""
    ws.pcm.get(_archive(ws.token))

    opened: list[str] = []
    real = pcm._install_size_of
    monkeypatch.setattr(
        pcm, "_install_size_of", lambda path: (opened.append(path), real(path))[1]
    )
    assert ws.pcm.get(_packages(ws.token)).status_code == 200
    assert opened == [], "a warm cache should be answered from the sidecar"


def test_an_unwritable_cache_still_serves_the_same_archive(
    db, ws: Tenant, tmp_path, monkeypatch
):
    """A read-only volume or a full disk costs the cache, not the
    feature. Both paths go through one `write_archive`, so the fallback
    has to produce the identical archive — otherwise `download_sha256`
    would depend on whether the disk happened to be writable."""
    from app.domain.workspaces.models import Workspace

    workspace = db.get(Workspace, ws.workspace_id)
    on_disk = pcm.build_package(db, ws=workspace)

    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x")
    monkeypatch.setattr(pcm, "_cache_dir", lambda _ws_id: str(blocker / "pcm"))

    in_memory = pcm.build_package(db, ws=workspace)
    assert not in_memory.serve_from_disk
    assert in_memory.data is not None
    assert hashlib.sha256(in_memory.data).hexdigest() == in_memory.sha256
    assert in_memory.download_size == len(in_memory.data)
    assert in_memory.sha256 == on_disk.sha256
    assert in_memory.install_size == on_disk.install_size


def test_a_truncated_cache_entry_is_rebuilt_rather_than_a_500(ws: Tenant):
    """A corrupt archive must not become a permanent 500 for the
    workspace — every later request would find the same bad file."""
    first = ws.pcm.get(_archive(ws.token))
    cache_dir = pathlib.Path(pcm._cache_dir(ws.workspace_id))
    cached = next(iter(cache_dir.glob("*.zip")))
    cached.write_bytes(first.content[: len(first.content) // 3])

    second = ws.pcm.get(_archive(ws.token))
    assert second.status_code == 200
    assert second.content == first.content


def test_a_cache_entry_that_is_not_a_zip_at_all_is_rebuilt(ws: Tenant):
    """Same path, reached through `_install_size_of` raising BadZipFile
    rather than through the sidecar's size check."""
    first = ws.pcm.get(_archive(ws.token))
    cache_dir = pathlib.Path(pcm._cache_dir(ws.workspace_id))
    cached = next(iter(cache_dir.glob("*.zip")))
    cached.write_bytes(b"definitely not a zip")
    for sidecar in cache_dir.glob("*.json"):
        sidecar.unlink()

    second = ws.pcm.get(_archive(ws.token))
    assert second.status_code == 200
    assert second.content == first.content


def test_builds_are_capped_so_a_burst_cannot_hold_n_packages(ws: Tenant, monkeypatch):
    """A build deflates every file the workspace owns. The threadpool is
    40 slots wide and the archive route allows 30/minute per address, so
    without a cap a burst of cache misses runs dozens of builds at once."""
    monkeypatch.setattr(pcm, "_BUILD_WAIT_SECONDS", 0.05)
    ws.symbol("R_ForcesABuild")  # new fingerprint → guaranteed cache miss

    held = [pcm._BUILD_SLOTS.acquire() for _ in range(2)]
    try:
        response = ws.pcm.get(_archive(ws.token))
        assert response.status_code == 503
        assert response.json()["code"] == "kicad.package_unavailable"
    finally:
        for _ in held:
            pcm._BUILD_SLOTS.release()

    # With the slots back, the same request succeeds.
    assert ws.pcm.get(_archive(ws.token)).status_code == 200


def test_a_cache_hit_is_served_without_a_build_slot(ws: Tenant, monkeypatch):
    """Hits neither build nor buffer, so they must not queue behind
    builds — otherwise two slow builds stall every warm reader."""
    ws.pcm.get(_archive(ws.token))
    monkeypatch.setattr(pcm, "_BUILD_WAIT_SECONDS", 0.05)

    held = [pcm._BUILD_SLOTS.acquire() for _ in range(2)]
    try:
        assert ws.pcm.get(_archive(ws.token)).status_code == 200
    finally:
        for _ in held:
            pcm._BUILD_SLOTS.release()


def test_the_cache_never_touches_another_workspaces_directory(
    ws: Tenant, other: Tenant
):
    other.symbol("OTHER_SYM")
    other.pcm.get(_archive(other.token))
    other_dir = pathlib.Path(pcm._cache_dir(other.workspace_id))
    assert len(list(other_dir.glob("*.zip"))) == 1

    ws.pcm.get(_archive(ws.token))
    ws.symbol("R_Churn")
    ws.pcm.get(_archive(ws.token))

    assert len(list(other_dir.glob("*.zip"))) == 1


def test_changing_content_changes_the_archive(ws: Tenant):
    """Even inside one version tick. The cache is keyed on a content
    fingerprint, not the version, so an edit two seconds after another
    still ships."""
    before = ws.pcm.get(_archive(ws.token)).content
    ws.symbol("R_New")
    assert ws.pcm.get(_archive(ws.token)).content != before


def test_the_version_advances_when_content_changes(ws: Tenant):
    """The PCM offers an update by comparing versions and nothing else.

    The existing content is backdated rather than the test waiting out a
    version tick: the property is "a later change sorts higher", not
    "the clock advances".
    """
    _backdate(ws)
    before = ws.version()
    ws.symbol("R_Later")
    assert _as_tuple(ws.version()) > _as_tuple(before)


def test_renaming_the_workspace_does_not_leave_a_stale_archive(ws: Tenant):
    """The package's name and descriptions come from the workspace, and
    they live in `metadata.json` INSIDE the zip as well as in
    `packages.json`. The workspace name is therefore part of the cache
    fingerprint: without it a rename would serve a freshly-computed
    `packages.json` beside a cached archive still claiming the old name.

    What this does NOT assert is a version bump. `workspaces` has no
    `updated_at` column, so `_latest_change` has nothing to read and
    KiCad won't re-fetch until the next real content change — a known,
    documented gap that needs a migration to close.
    """
    renamed = ws.session.patch("/api/workspaces/current", json={"name": "Renamed Co"})
    assert renamed.status_code == 200, renamed.text

    package = ws.packages()["packages"][0]
    assert package["name"] == "Renamed Co (stockManager)"

    response = ws.pcm.get(_archive(ws.token))
    archive = _open_zip(response)
    assert json.loads(archive.read("metadata.json"))["name"] == package["name"]
    # The published digest still describes the archive actually served.
    assert package["versions"][0]["download_sha256"] == (
        hashlib.sha256(response.content).hexdigest()
    )


def test_linking_a_3d_model_advances_the_version(ws: Tenant):
    """`eda_footprint_models` has no timestamps of its own, so the link
    has to bump the footprint's — otherwise attaching a model changes the
    package's contents without ever offering anyone the update.

    The failure mode this pins is subtle: the footprint row is only
    marked dirty if something on it actually changes, and re-linking sets
    `updated_by` to the value it already had. Uploading and linking as
    the same user — which is the normal case — is exactly when that bites.
    """
    footprint = ws.session.get("/api/eda/footprints").json()["data"][0]
    datafile = [
        row
        for row in ws.session.get("/api/eda/datafiles").json()["data"]
        if row["kind"] == "step"
    ][0]

    _backdate(ws)
    before = ws.version()
    r = ws.session.post(
        f"/api/eda/footprints/{footprint['id']}/models",
        json={"datafile_id": datafile["id"], "position": 0},
    )
    assert r.status_code == 200, r.text
    assert _as_tuple(ws.version()) > _as_tuple(before)


def _backdate(tenant: Tenant, *, seconds: int = 3600) -> None:
    """Age everything the version is derived from.

    Stands in for elapsed wall-clock time, so a test can show that the
    NEXT change sorts higher without sleeping through a version tick.
    """
    from app.domain.categories.models import PartCategory
    from app.domain.eda.models import EdaDatafile, EdaFootprint
    from app.infra.db import SessionLocal

    when = utcnow() - timedelta(seconds=seconds)
    session = SessionLocal()
    try:
        for Model in (EdaSymbol, EdaFootprint, EdaDatafile, PartCategory):
            session.execute(
                update(Model)
                .where(Model.workspace_id == tenant.workspace_id)
                .values(updated_at=when)
            )
        session.commit()
    finally:
        session.close()


def _as_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


# ---------------------------------------------------------------------
# Version derivation, as a function
# ---------------------------------------------------------------------


def test_derive_version_is_monotonic():
    moments = [
        pcm.VERSION_EPOCH,
        pcm.VERSION_EPOCH + timedelta(seconds=2),
        pcm.VERSION_EPOCH + timedelta(hours=23, minutes=59, seconds=59),
        pcm.VERSION_EPOCH + timedelta(days=1),
        pcm.VERSION_EPOCH + timedelta(days=1, seconds=2),
        pcm.VERSION_EPOCH + timedelta(days=9999, hours=23),
        # Past the four digits the schema gives the minor field: the days
        # roll into the major rather than being clamped, so the sequence
        # keeps growing instead of flattening in 2053.
        pcm.VERSION_EPOCH + timedelta(days=10_000),
        pcm.VERSION_EPOCH + timedelta(days=10_001),
    ]
    versions = [pcm._derive_version(moment)[0] for moment in moments]
    assert versions == sorted(versions, key=_as_tuple)
    assert len(set(versions)) == len(versions)


def test_derive_version_shape_at_a_day_boundary():
    assert pcm._derive_version(pcm.VERSION_EPOCH)[0] == "1.0.0"
    end_of_day = pcm.VERSION_EPOCH + timedelta(hours=23, minutes=59, seconds=59)
    assert pcm._derive_version(end_of_day)[0] == "1.0.43199"
    assert pcm._derive_version(pcm.VERSION_EPOCH + timedelta(days=1))[0] == "1.1.0"
    assert pcm._derive_version(pcm.VERSION_EPOCH + timedelta(days=10_000))[0] == "2.0.0"


def test_derive_version_clamps_below_the_epoch():
    """A restored backup or a skewed clock must not produce a negative
    component — the PCM's version pattern rejects the whole document."""
    stale = datetime(2020, 6, 1, tzinfo=timezone.utc)
    assert pcm._derive_version(stale)[0] == "1.0.0"
    assert pcm._derive_version(None)[0] == "1.0.0"


# ---------------------------------------------------------------------
# The documents, against KiCad's own v2 schema
# ---------------------------------------------------------------------

# Verbatim from kicad/pcm/schemas/pcm.v2.schema.json.
_IDENTIFIER_PATTERN = r"^[a-zA-Z][-a-zA-Z0-9.]{0,98}[a-zA-Z0-9]$"
_VERSION_PATTERN = r"^\d{1,4}(\.\d{1,4}(\.\d{1,6})?)?$"
_KICAD_VERSION_PATTERN = r"^\d{1,2}(\.\d{1,2}(\.\d{1,2})?)?$"
_SHA256_PATTERN = r"^[a-f0-9]{64}$"
_UPDATE_TIME_PATTERN = r"^2\d\d\d-\d\d-\d\d \d\d:\d\d:\d\d$"
_TYPE_PATTERN = r"^[a-z][-a-z0-9]{0,48}[a-z0-9]$"


def test_repository_document_shape(ws: Tenant):
    document = ws.pcm.get(_repository(ws.token)).json()
    assert set(document) >= {"name", "packages"}
    assert document["maintainer"]["name"]
    assert isinstance(document["maintainer"]["contact"], dict)

    packages = document["packages"]
    assert set(packages) == {"url", "sha256", "update_timestamp", "update_time_utc"}
    assert packages["url"] == (
        f"http://localhost:5173/kicad-api/pcm/{ws.token}/packages.json"
    )
    assert re.match(_SHA256_PATTERN, packages["sha256"])
    assert isinstance(packages["update_timestamp"], int)
    assert re.match(_UPDATE_TIME_PATTERN, packages["update_time_utc"])


def test_repository_sha_matches_the_packages_bytes_actually_served(ws: Tenant):
    """The PCM verifies this digest against what it downloads, so it has
    to cover the exact bytes — not a re-serialisation of the same data."""
    advertised = ws.pcm.get(_repository(ws.token)).json()["packages"]["sha256"]
    served = ws.pcm.get(_packages(ws.token)).content
    assert hashlib.sha256(served).hexdigest() == advertised


def test_packages_document_shape(ws: Tenant):
    package = ws.packages()["packages"][0]
    assert set(package) >= {
        "name",
        "description",
        "description_full",
        "identifier",
        "type",
        "author",
        "license",
        "resources",
        "versions",
    }
    assert re.match(_IDENTIFIER_PATTERN, package["identifier"])
    assert re.match(_TYPE_PATTERN, package["type"])
    assert package["type"] == "library"
    assert package["author"]["name"] and isinstance(package["author"]["contact"], dict)
    assert isinstance(package["resources"], dict)
    assert len(package["name"]) <= 200
    assert len(package["description"]) <= 500
    assert len(package["description_full"]) <= 5000

    version = package["versions"][0]
    assert re.match(_VERSION_PATTERN, version["version"])
    assert version["status"] == "stable"
    assert re.match(_KICAD_VERSION_PATTERN, version["kicad_version"])
    assert re.match(_SHA256_PATTERN, version["download_sha256"])


def test_download_size_and_install_size_describe_the_archive(ws: Tenant):
    version = ws.packages()["packages"][0]["versions"][0]
    response = ws.pcm.get(_archive(ws.token))

    assert version["download_url"] == (
        f"http://localhost:5173/kicad-api/pcm/{ws.token}/package.zip"
    )
    assert version["download_size"] == len(response.content)

    archive = zipfile.ZipFile(io.BytesIO(response.content))
    assert version["install_size"] == sum(
        info.file_size for info in archive.infolist()
    )
    assert version["install_size"] > version["download_size"] or archive.namelist()


# ---------------------------------------------------------------------
# Conformance against KiCad's own schema
#
# The regex assertions above pin fields we chose to care about. This
# section pins the whole document against the file KiCad validates with,
# which is the only way to catch a field we never thought to assert on.
#
# It is not theoretical. `license: "proprietary"` reads as the obvious
# label for a private workspace's libraries and is accepted by the v2
# schema — but v1 closes `license` to a 90-value enum, and
# `PLUGIN_CONTENT_MANAGER::ValidateJson` rejects the ENTIRE document over
# one bad value. The repository silently failed to load in a real KiCad
# while every hand-written assertion here passed.
#
# v1 rather than v2 deliberately: v1 is the stricter of the two on every
# field where they differ (`license`, `type`), so a document that
# satisfies it satisfies both — and it is what a KiCad predating v2 uses,
# which `kicad_version: "8.0"` promises we support.
# ---------------------------------------------------------------------

_SCHEMA_PATH = pathlib.Path(__file__).parent / "fixtures" / "pcm.v1.schema.json"


def _schema() -> dict:
    return json.loads(_SCHEMA_PATH.read_text())


def _validate(document: dict, definition: str) -> None:
    """Validate `document` against one definition in the vendored schema.

    The schema's root `$ref` is `#/definitions/Package`, so a Repository
    or a PackageArray has to be addressed by name with the whole file as
    the resolution base.
    """
    schema = _schema()
    jsonschema.validate(
        instance=document,
        schema={**schema, "$ref": f"#/definitions/{definition}"},
    )


def test_the_vendored_schema_is_the_one_that_rejected_proprietary():
    """Guards the fixture itself.

    If a refresh ever loosened `license` into a free string, every
    conformance test below would keep passing while testing nothing —
    exactly the failure that shipped the bug.
    """
    licenses = _schema()["definitions"]["License"]["enum"]
    assert "proprietary" not in licenses
    assert pcm.PACKAGE_LICENSE in licenses


@pytest.mark.parametrize("populated", [True, False], ids=["populated", "empty"])
def test_served_documents_validate_against_kicads_schema(db, populated: bool):
    """The LIVE bytes, both documents, both workspace states."""
    tenant = _stocked(Tenant()) if populated else Tenant()

    repository = json.loads(tenant.pcm.get(_repository(tenant.token)).content)
    _validate(repository, "Repository")

    packages = json.loads(tenant.pcm.get(_packages(tenant.token)).content)
    _validate(packages, "PackageArray")
    assert bool(packages["packages"]) is populated


@pytest.mark.parametrize("populated", [True, False], ids=["populated", "empty"])
def test_in_zip_metadata_validates_against_kicads_schema(db, populated: bool):
    """`metadata.json` is validated too, on the install-from-file path —
    where there is no `packages.json` to have caught the problem first."""
    tenant = _stocked(Tenant()) if populated else Tenant()
    archive = _open_zip(tenant.pcm.get(_archive(tenant.token)))
    _validate(json.loads(archive.read("metadata.json")), "Package")


def test_the_archive_is_served_as_a_zip(ws: Tenant):
    response = ws.pcm.get(_archive(ws.token))
    assert response.headers["content-type"] == "application/zip"
    # The URL is a credential; nothing between here and KiCad should keep
    # a copy of the response, and no crawler should index the page.
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"


# ---------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------


@pytest.fixture
def limiter_enabled():
    """slowapi is off outside prod so the suite can hammer endpoints.

    Turning it on for one test is the only way to exercise the wiring,
    and the bucket store is process-global, so it is reset on both sides.
    """
    original = _ratelimit_mod.limiter.enabled
    _ratelimit_mod.limiter.enabled = True
    _ratelimit_mod.limiter.reset()
    try:
        yield
    finally:
        _ratelimit_mod.limiter.enabled = original
        _ratelimit_mod.limiter.reset()


def test_a_bad_token_flood_is_rate_limited(db, limiter_enabled):
    """The token is resolved in the route body precisely so slowapi's
    wrapper runs first — as a dependency it would 404 the flood before
    the limiter ever saw it, and stuffing would be free."""
    client = TestClient(app)
    path = _archive(f"smk_{uuid.uuid4().hex}.wrong")
    statuses = {client.get(path).status_code for _ in range(40)}
    assert 429 in statuses


def test_the_rate_limit_bucket_is_not_the_raw_token():
    """Bucket keys outlive the request in slowapi's store. A prefix of a
    live credential is not something to leave sitting there."""
    from app.api.routes import kicad_pcm

    class _Request:
        path_params = {"token": "smk_abc.supersecret"}

    key = kicad_pcm._token_key(_Request())
    assert key.startswith("pcm:")
    assert "supersecret" not in key
    assert "smk_abc" not in key
