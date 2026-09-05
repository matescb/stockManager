"""Talk to a cab SQUIX printer over raw socket (default port 9100).

The wire protocol is JScript: plain ASCII commands, CR/LF terminated.
Bidirectional commands (ESCs status, q<x> queries) use the same socket
and return short text replies.

Reference: cab JScript Programming Manual, Edition 05/2025 (firmware 5.46.3).

PROVENANCE: vendored unmodified from the MIT-licensed cab_squix toolkit at
/mnt/data/WORK/cab. This copy was re-vendored from the sibling skladVA project
(/mnt/data/WORK/sklad, ``backend/app/printing/cab_squix/``), which vendored it
first; both projects drive the same physical cab SQUIX printer. See
``app/domain/printing/cab_squix/__init__.py`` for details.
"""
from __future__ import annotations

import socket
import time
from dataclasses import dataclass

DEFAULT_HOST = "192.168.1.249"
DEFAULT_PORT = 9100

ESC = b"\x1b"

# ESCs status response field meanings (manual section 2.19, page 29).
# Keyed by the single error character returned in the second position.
STATUS_ERROR_CODES: dict[str, str] = {
    "-": "no error",
    "a": "applicator did not reach upper position",
    "b": "applicator did not reach lower position",
    "c": "vacuum plate is empty",
    "d": "label not deposited",
    "e": "host stop/error",
    "f": "reflective sensor blocked",
    "g": "tamp pad 90 deg error",
    "h": "tamp pad 0 deg error",
    "i": "table not in front position",
    "j": "table not in rear position",
    "k": "head lifted",
    "l": "head down",
    "m": "scan result negative",
    "n": "global network error",
    "o": "compressed air error",
    "r": "RFID error",
    "s": "system fault",
    "u": "USB error",
    "x": "stacker full, printer paused",
    "A": "applicator error",
    "B": "protocol error / invalid barcode data",
    "C": "memory card error",
    "D": "printhead or pinch roller open",
    "E": "synchronization error (no label found)",
    "F": "out of ribbon",
    "G": "PPP reload required",
    "H": "heating voltage problem",
    "I": "cutter jammed",
    "N": "label material too thick (cutter)",
    "O": "out of memory",
    "P": "out of paper",
    "R": "ribbon detected in thermal direct mode",
    "S": "ribbon saver malfunction",
    "U": "abc user error",
    "V": "input buffer overflow",
    "W": "print head overheated",
    "X": "external I/O error",
    "Y": "printhead error",
    "Z": "printhead damaged",
}


@dataclass(frozen=True)
class Status:
    """Parsed reply to the ESCs printer-status query.

    The wire format is exactly 9 ASCII characters ``XYNNNNNNZ`` where:
        X = Y/N online flag
        Y = single-character error code (see STATUS_ERROR_CODES)
        NNNNNN = 6-digit pending label count
        Z = Y/N interpreter-active flag (a job is running)
    """
    online: bool
    error_code: str
    error_text: str
    pending_labels: int
    job_active: bool
    raw: str

    @classmethod
    def parse(cls, raw: str) -> "Status":
        s = raw.strip()
        if len(s) != 9:
            raise ValueError(f"unexpected ESCs reply: {raw!r}")
        return cls(
            online=s[0] == "Y",
            error_code=s[1],
            error_text=STATUS_ERROR_CODES.get(s[1], f"unknown code {s[1]!r}"),
            pending_labels=int(s[2:8]),
            job_active=s[8] == "Y",
            raw=s,
        )


class PrinterError(RuntimeError):
    """Raised when the printer reports an error before or during a job."""


class CabPrinter:
    """Stateless client. Each method opens a new socket, sends, and closes.

    Methods come in three groups:
        status() / query() / cancel()   -- short bidirectional commands
        preflight()                     -- read status, raise if not ready
        send_job()                      -- write JScript and (optionally) wait

    The host is reachable on the LAN; firewall rules must permit TCP/9100.
    """

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                 timeout: float = 3.0):
        self.host = host
        self.port = port
        self.timeout = timeout

    # ----- low level ------------------------------------------------------

    def _open(self) -> socket.socket:
        s = socket.create_connection((self.host, self.port), timeout=self.timeout)
        s.settimeout(self.timeout)
        return s

    def _send(self, data: bytes) -> None:
        with self._open() as s:
            s.sendall(data)

    def _ask(self, cmd: bytes, read_timeout: float = 1.5,
             max_bytes: int = 4096) -> bytes:
        with self._open() as s:
            s.sendall(cmd)
            s.settimeout(read_timeout)
            buf = b""
            try:
                while True:
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                    if len(buf) >= max_bytes:
                        break
            except socket.timeout:
                pass
            return buf

    # ----- queries --------------------------------------------------------

    def status(self) -> Status:
        """ESCs query (manual section 2.19). Returns a parsed :class:`Status`."""
        return Status.parse(self._ask(ESC + b"s").decode("ascii", errors="replace"))

    def query(self, what: str) -> str:
        """``q<type>`` query (manual section 3.11). Returns the raw reply text.

        Examples::

            printer.query("v")   # firmware: "5.44.2 Mar 04, 2024 (SQUIX 4/300P)"
            printer.query("w")   # roll diameter mm, or "-1" if not measured
            printer.query("r")   # ribbon diameter mm
            printer.query("p")   # peripheral type: NONE | CUTTER | REWINDER | ...
            printer.query("o")   # full lifetime statistics (X4 only)
            printer.query("f")   # free memory: "195223552 bytes free"
            printer.query("t")   # date/time: "YYMMDDhhmmss"
            printer.query("m")   # default memory card type
        """
        what = what[1:] if what.startswith("q") else what
        raw = self._ask(b"q" + what.encode() + b"\r")
        return raw.decode("utf-8", errors="replace").rstrip("\r\n")

    def cancel(self) -> None:
        """ESCt (manual section 2.20). Clears input buffer + resets errors.

        Manual mandates a >= 1 s pause before sending more data; we sleep here
        so callers can fire-and-forget.
        """
        self._send(ESC + b"t")
        time.sleep(1.1)

    # ----- printing -------------------------------------------------------

    def preflight(self) -> Status:
        """Read status and raise :class:`PrinterError` only on a real fault.

        The SQUIX ``ESCs`` "online" flag (first status char) is NOT a
        printability signal: an idle, perfectly ready SQUIX routinely reports
        ``N`` here yet still accepts and runs jobs — verified on the live
        printer, where ``job_active`` toggles on send regardless of the flag.
        Gating on it rejected a healthy printer with a false "printer offline"
        (the job never even reached the device). A genuinely unreachable or
        powered-off printer fails the socket connection long before this check,
        so the flag adds no safety. We therefore gate only on an actual error
        code (out of paper/ribbon, head open, …) and on another job already
        running — never on the bare online flag.
        """
        st = self.status()
        if st.error_code != "-":
            raise PrinterError(
                f"printer in error state {st.error_code} ({st.error_text})"
            )
        if st.job_active:
            raise PrinterError("printer is currently running another job")
        return st

    def send_job(
        self,
        jscript: str,
        *,
        skip_preflight: bool = False,
        wait_for_completion: bool = True,
        poll_interval: float = 0.5,
        max_wait: float = 60.0,
    ) -> Status:
        """Send a JScript job and optionally wait until the interpreter idles.

        Line endings in ``jscript`` are normalized to CRLF (manual section 1.3
        accepts CR/LF/CRLF; CRLF is what worked first-shot in our tests).
        """
        if not skip_preflight:
            self.preflight()

        normalized = jscript.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
        if not normalized.endswith("\r\n"):
            normalized += "\r\n"
        self._send(normalized.encode("utf-8"))

        if not wait_for_completion:
            return self.status()

        deadline = time.time() + max_wait
        saw_active = False
        while time.time() < deadline:
            time.sleep(poll_interval)
            st = self.status()
            if st.error_code != "-":
                raise PrinterError(
                    f"job failed: code {st.error_code} ({st.error_text})"
                )
            if st.job_active:
                saw_active = True
            elif saw_active:
                return st
        raise PrinterError(f"job did not finish within {max_wait:.0f}s")
