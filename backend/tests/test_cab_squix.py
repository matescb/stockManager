"""Unit tests for the vendored cab SQUIX JScript element model + preflight.

DB-free. Two groups:

  * The JScript element model (``Text`` / ``Barcode1D`` / ``Barcode2D`` / a
    rendered ``Job``) emits the wire syntax from the cab JScript manual.
  * ``CabPrinter.preflight`` gates on real faults, not the bare online flag —
    ported from skladVA's ``tests/unit/test_cab_preflight.py`` (regression guard
    for the false "printer offline" bug: the live SQUIX reports an ``N`` online
    flag while idle yet still prints).
"""

from __future__ import annotations

import pytest

from app.domain.printing.cab_squix import Barcode1D, Barcode2D, Job, Text
from app.domain.printing.cab_squix.printer import CabPrinter, PrinterError, Status

# ---------------------------------------------------------------------------
# element model -> JScript lines
# ---------------------------------------------------------------------------


def test_text_element_emits_T_command():
    # T[:name;]x,y,r,font,size;text
    line = Text(2, 3, "Hello", font=5, size_pt=12).to_jscript()
    assert line == "T 2,3,0,5,pt12;Hello"


def test_text_element_accepts_named_truetype_font():
    # The font position accepts a loaded TrueType font name, not just an int id.
    line = Text(1, 1, "x", font="skautbold", size_pt=8).to_jscript()
    assert line == "T 1,1,0,skautbold,pt8;x"


def test_barcode1d_default_is_code128_without_ratio():
    line = Barcode1D(5, 5, "12345", height_mm=8, ne_mm=0.4).to_jscript()
    # B x,y,r,type[+options],height,ne;text — no ratio field for Code 128.
    assert line == "B 5,5,0,CODE128,8,0.4;12345"


def test_barcode1d_includes_ratio_only_when_set():
    line = Barcode1D(
        0, 0, "ABC", type="CODE39", height_mm=10, ne_mm=0.3, ratio=2.5, options="+MOD10"
    ).to_jscript()
    assert line == "B 0,0,0,CODE39+MOD10,10,0.3,2.5;ABC"


def test_barcode2d_qrcode_uses_dotsize():
    line = Barcode2D(2, 2, "/i/ABC12", dotsize_mm=0.5).to_jscript()
    # B x,y,r,QRCODE,dotsize;text
    assert line == "B 2,2,0,QRCODE,0.5;/i/ABC12"


def test_barcode2d_pdf417_uses_height_ne_ratio_not_dotsize():
    line = Barcode2D(
        1,
        1,
        "data",
        type="PDF417",
        pdf417_height_mm=0.2,
        pdf417_ne_mm=0.4,
        pdf417_ratio=3,
    ).to_jscript()
    assert line == "B 1,1,0,PDF417,0.2,0.4,3;data"


# ---------------------------------------------------------------------------
# Job -> full JScript program
# ---------------------------------------------------------------------------


def test_job_renders_header_body_and_print_count():
    job = Job(width_mm=104, height_mm=68, gap_mm=3)
    job.add(Text(2, 2, "Label"), Barcode2D(2, 10, "/i/XYZ", dotsize_mm=0.5))

    program = job.to_jscript()
    lines = program.split("\r\n")

    # Header: units, start-of-job, print params, geometry.
    assert lines[0] == "m m"
    assert lines[1] == "J"
    assert lines[2] == "H 100,0,T"
    # S ptype;xo,yo,height,pitch,width — pitch = height + gap for die-cut (l1).
    assert lines[3] == "S l1;0,0,68,71,104"
    # Body elements in insertion order.
    assert lines[4] == "T 2,2,0,3,pt10;Label"
    assert lines[5] == "B 2,10,0,QRCODE,0.5;/i/XYZ"
    # Footer print-count, then a trailing CRLF (empty final split element).
    assert lines[6] == "A 1"
    assert program.endswith("\r\n")


def test_job_pitch_equals_height_for_endless_material():
    job = Job(width_mm=50, height_mm=30, gap_mm=3, ptype="e")
    # Endless material has no gap — pitch collapses to the label height.
    assert job.pitch_mm == 30
    assert "S e;0,0,30,30,50" in job.to_jscript()


def test_job_copies_render_in_print_count():
    job = Job(width_mm=50, height_mm=30, copies=4)
    assert job.to_jscript().rstrip().endswith("A 4")


def test_job_add_and_extend_are_chainable():
    job = Job(width_mm=10, height_mm=10)
    returned = job.add(Text(0, 0, "a")).extend([Text(1, 1, "b")])
    assert returned is job
    assert len(job.elements) == 2


# ---------------------------------------------------------------------------
# CabPrinter.preflight — gate on real faults, not the bare online flag
# ---------------------------------------------------------------------------


def _printer_reporting(raw: str, monkeypatch: pytest.MonkeyPatch) -> CabPrinter:
    p = CabPrinter("printer.test", 9100)
    monkeypatch.setattr(p, "status", lambda: Status.parse(raw))
    return p


def test_preflight_allows_idle_printer_with_offline_flag(monkeypatch):
    # N online flag, error '-', not busy: a healthy idle SQUIX — must pass.
    p = _printer_reporting("N-000000N", monkeypatch)
    st = p.preflight()
    assert st.online is False  # flag is N, but the job is allowed through
    assert st.error_code == "-"


def test_preflight_allows_online_flag(monkeypatch):
    p = _printer_reporting("Y-000000N", monkeypatch)
    assert p.preflight().raw == "Y-000000N"


def test_preflight_raises_on_real_error_code(monkeypatch):
    # 'P' = out of paper — a genuine fault that must block the send.
    p = _printer_reporting("NP000000N", monkeypatch)
    with pytest.raises(PrinterError, match="out of paper"):
        p.preflight()


def test_preflight_raises_when_another_job_is_running(monkeypatch):
    # Last char Y = interpreter active (a job is in flight).
    p = _printer_reporting("Y-000001Y", monkeypatch)
    with pytest.raises(PrinterError, match="another job"):
        p.preflight()


def test_status_parse_rejects_malformed_reply():
    # A reply that is not exactly 9 chars must be rejected up front.
    with pytest.raises(ValueError, match="unexpected ESCs reply"):
        Status.parse("YN")
