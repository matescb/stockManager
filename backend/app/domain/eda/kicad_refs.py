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

Both path variables are KiCad path-substitution variables, defined by
the phase-6 PCM package. Storing the variable rather than an absolute
path is what lets one package work on every machine that installs it.
"""
from __future__ import annotations

from typing import Protocol

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
