"""The stockManager → KiCad naming contract. One module, two consumers.

Phase 5 (`api/routes/kicad.py`) *serves* these strings over the KiCad
HTTP-library protocol; phase 6 *generates* the library files they name.
If the two disagree by a single character KiCad reports a broken symbol
on every part in the workspace, so both sides import from here rather
than formatting their own strings.

The contract
------------

* **Library stem** — one generated library per part-category, whose
  file is named ``SM_<library_slug>`` (``SM_resistors.kicad_sym``,
  ``SM_resistors.pretty/``). Symbols and footprints whose row has no
  category — or whose category has been hard-deleted, which nulls the
  FK — land in ``SM_uncategorized``. This is what phase 6 names files
  and package entries with.

* **Library nickname** — ``PCM_SM_<library_slug>``, i.e. ``"PCM_"``
  plus the stem. **The prefix is not ours.** KiCad's Plugin & Content
  Manager auto-registers the libraries in an installed package into
  ``sym-lib-table`` / ``fp-lib-table``, and it derives the nickname by
  prepending ``PCM_`` to the file stem. Phase 6 ships our libraries
  through the PCM, so ``SM_resistors.kicad_sym`` is registered on the
  user's machine as ``PCM_SM_resistors`` and nothing else. A reference
  naming the bare stem resolves against no registered library, which
  KiCad reports as a broken symbol on every part that uses it — the
  breakage class described in
  https://forum.kicad.info/t/pcm-content-library-and-library-prefix-problem/63784.

  Symbol and footprint libraries share the nickname because KiCad keeps
  them in separate tables: the same nickname in both is unambiguous,
  and it means one category maps to one name a user can recognise in
  either chooser.

* **Entry reference** — ``<nickname>:<entry name>``, KiCad's
  ``LibNick:Entry`` form. Built on the nickname, never the stem.

  The slug comes from the **symbol's or footprint's own** category, NOT
  from the category of the part that points at it. A resistor part in
  "Passives" may well use a symbol filed under "Generic"; the symbol
  ships in the library its own row names, so that is the library the
  reference has to name too.

* **3D model path** — ``${STOCKMGR_3D}/<datafile name>``. Stored inside
  the footprint's ``(model …)`` node by the phase-3 importer, which
  imports :data:`MODEL_PATH_VAR` from here.

* **SPICE library path** — ``${STOCKMGR_SPICE}/<datafile name>``,
  emitted as the ``Sim.Library`` symbol field.

Both path variables are KiCad path-substitution variables. Storing the
variable rather than an absolute path is what lets one set of bytes work
on every machine that reads them.

Where the PCM puts it all
-------------------------

``${STOCKMGR_3D}`` is what a user gets when they download a footprint
from the CAD tab and wire the variable up by hand. The phase-6 PCM
package can do better, because the install location is known:
``pcm.py`` rewrites those paths to :func:`pcm_model_path` on the way
into the zip, so an installed package resolves its models with no
configuration at all. (``Sim.Library`` can't get the same treatment — it
is served as JSON by phase 5, not stored in bytes we package — so
``${STOCKMGR_SPICE}`` stays a variable the user points at
:func:`pcm_spice_dir`.)

The layout is fixed by KiCad, not by us —
``kicad/pcm/pcm_task_manager.cpp::extract()`` rewrites every archive
member ``<folder>/<rest>`` to ``<3rd-party root>/<folder>/<id>/<rest>``::

    wxString clean_package_id = aPackageId;
    clean_package_id.Replace( '.', '_' );
    …
    path_parts.Insert( clean_package_id, 1 );

so ``3dmodels/foo.step`` in the zip lands at
``<root>/3dmodels/com_stockmanager_<ws>/foo.step`` — the identifier sits
BELOW the folder, and its dots become underscores
(:func:`install_dir_name`). ``<folder>`` must be one of KiCad's
``PCM_PACKAGE_DIRECTORIES`` (``kicad/pcm/pcm.h``: plugins, footprints,
3dmodels, symbols, resources, colors, templates, scripts); a member
under anything else is silently skipped by the extractor.

:data:`THIRD_PARTY_VAR` names that root, and it is **versioned** —
``KICAD8_3RD_PARTY`` on KiCad 8, ``KICAD9_3RD_PARTY`` on 9. There is no
unversioned spelling, and a hand-created ``KICAD_3RD_PARTY`` is not
recognised. Hard-coding one major is nonetheless correct, because
``common/common.cpp::KIwxExpandEnvVars`` exists for exactly this case: a
``${KICADn_3RD_PARTY}`` it cannot resolve is recognised by
``ENV_VAR::IsVersionedEnvVar( strVarName, "3RD_PARTY" )`` and re-pointed
at whichever ``KICAD*_3RD_PARTY`` the running version defines. Real
packages rely on it and pin whatever major they were built against —
Espressif's ships
``${KICAD8_3RD_PARTY}/3dmodels/com_github_espressif_kicad-libraries/…``.
We pin 8 to match the ``kicad_version`` floor the package advertises.

``.3dshapes`` — the conventional suffix on 3D-model directories in
KiCad's own libraries — is NOT required by anything, so models ship flat
under ``3dmodels/``. ``.pretty`` on a footprint directory genuinely is:
``common/libraries/library_manager.cpp::PCM_LIB_TRAVERSER`` matches that
suffix and nothing else when it registers installed libraries, and it is
the same traverser that prepends :data:`PCM_NICKNAME_PREFIX`.
"""
from __future__ import annotations

from typing import Protocol
from uuid import UUID

__all__ = [
    "LIBRARY_PREFIX",
    "PCM_NICKNAME_PREFIX",
    "UNCATEGORIZED_SLUG",
    "MODEL_PATH_VAR",
    "SPICE_PATH_VAR",
    "package_stem",
    "library_nickname",
    "symbol_lib_nickname",
    "footprint_lib_nickname",
    "entry_ref",
    "symbol_ref",
    "footprint_ref",
    "model_path",
    "spice_path",
    "PCM_IDENTIFIER_PREFIX",
    "THIRD_PARTY_VAR",
    "SYMBOLS_DIR",
    "FOOTPRINTS_DIR",
    "MODELS_DIR",
    "RESOURCES_DIR",
    "SPICE_SUBDIR",
    "PRETTY_SUFFIX",
    "package_identifier",
    "install_dir_name",
    "pcm_model_path",
    "pcm_spice_dir",
]

# Namespaces every generated library so it can't collide with a stock
# KiCad library or with one the user installed themselves.
LIBRARY_PREFIX = "SM_"

# Prepended by KiCad's Plugin & Content Manager when it registers an
# installed package's libraries. We don't choose it and can't opt out of
# it — we only have to predict it. See the module docstring.
PCM_NICKNAME_PREFIX = "PCM_"

# The bucket for rows with no category. A real category could in
# principle slugify to this same value; that collision merges the two
# libraries rather than breaking either, which is why it's tolerated
# instead of guarded.
UNCATEGORIZED_SLUG = "uncategorized"

MODEL_PATH_VAR = "${STOCKMGR_3D}"
SPICE_PATH_VAR = "${STOCKMGR_SPICE}"


class _NamedEntry(Protocol):
    """An `EdaSymbol` / `EdaFootprint` row — only `name` is read."""

    name: str


def package_stem(category_slug: str | None) -> str:
    """``SM_<slug>``, or ``SM_uncategorized`` when there is no category.

    The FILE name phase 6 writes — ``<stem>.kicad_sym``,
    ``<stem>.pretty/``. Never a reference; see `library_nickname`.
    """
    return f"{LIBRARY_PREFIX}{category_slug or UNCATEGORIZED_SLUG}"


def library_nickname(category_slug: str | None) -> str:
    """``PCM_SM_<slug>`` — the nickname the PCM will register the stem under.

    What every `LibNick:Entry` reference has to name. Used for both the
    symbol and the footprint library of a category — see the module
    docstring for why the prefix is not optional.
    """
    return f"{PCM_NICKNAME_PREFIX}{package_stem(category_slug)}"


# Spelled out at the call site so phase 6's library generation reads as
# "the symbol library for this category" rather than "the library".
symbol_lib_nickname = library_nickname
footprint_lib_nickname = library_nickname


def entry_ref(name: str, category_slug: str | None) -> str:
    """KiCad ``LibNick:Entry`` for an entry filed under *category_slug*."""
    return f"{library_nickname(category_slug)}:{name}"


def symbol_ref(symbol: _NamedEntry, category_slug: str | None) -> str:
    """The `symbolIdStr` for a hosted symbol.

    *category_slug* must be the slug of the SYMBOL row's own category.
    """
    return entry_ref(symbol.name, category_slug)


def footprint_ref(footprint: _NamedEntry, category_slug: str | None) -> str:
    """The `Footprint` field value for a hosted footprint.

    *category_slug* must be the slug of the FOOTPRINT row's own category.
    """
    return entry_ref(footprint.name, category_slug)


def model_path(name: str) -> str:
    """The ``(model …)`` path for a 3D datafile named *name*."""
    return f"{MODEL_PATH_VAR}/{name}"


def spice_path(name: str) -> str:
    """The ``Sim.Library`` value for a SPICE datafile named *name*."""
    return f"{SPICE_PATH_VAR}/{name}"


# ---------------------------------------------------------------------
# PCM package naming — see "Where the PCM puts it all" above
# ---------------------------------------------------------------------

# Reverse-DNS, as the PCM asks for. The full identifier is 49 characters
# (17 + a 32-hex workspace id), comfortably inside the 100 the v2 schema
# allows, and matches its `^[a-zA-Z][-a-zA-Z0-9.]{0,98}[a-zA-Z0-9]$`.
PCM_IDENTIFIER_PREFIX = "com.stockmanager."

# KiCad's 3rd-party install root. Versioned on purpose — read the module
# docstring before "fixing" the 8.
THIRD_PARTY_VAR = "${KICAD8_3RD_PARTY}"

# The top-level zip folders we use, all of them in KiCad's
# PCM_PACKAGE_DIRECTORIES allow-list.
SYMBOLS_DIR = "symbols"
FOOTPRINTS_DIR = "footprints"
MODELS_DIR = "3dmodels"
RESOURCES_DIR = "resources"

# `resources/` is a free-for-all as far as the PCM is concerned, so SPICE
# models get a subdirectory of their own inside it. There is no native
# slot for them.
SPICE_SUBDIR = "spice"

PRETTY_SUFFIX = ".pretty"


def package_identifier(workspace_id: UUID) -> str:
    """The PCM identifier for a workspace's one-and-only package.

    One package per workspace, not one per category: the PCM's unit of
    installation is the package, and a user should click Install once.
    """
    return f"{PCM_IDENTIFIER_PREFIX}{workspace_id.hex}"


def install_dir_name(identifier: str) -> str:
    """``com.stockmanager.<hex>`` → ``com_stockmanager_<hex>``.

    The directory the PCM extracts a package into. Not cosmetic — a path
    written with the dots left in resolves to nothing on an installed
    machine.
    """
    return identifier.replace(".", "_")


def pcm_model_path(identifier: str, name: str) -> str:
    """The installed location of a 3D datafile, as a ``(model …)`` path."""
    return f"{THIRD_PARTY_VAR}/{MODELS_DIR}/{install_dir_name(identifier)}/{name}"


def pcm_spice_dir(identifier: str) -> str:
    """Where installed SPICE models land — what ``${STOCKMGR_SPICE}``
    should be set to once the package is installed."""
    return (
        f"{THIRD_PARTY_VAR}/{RESOURCES_DIR}/"
        f"{install_dir_name(identifier)}/{SPICE_SUBDIR}"
    )
