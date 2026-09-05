"""Label-printing domain: the reusable cab SQUIX transport + print-job ledger.

This is the *foundation* layer only — the vendored cab SQUIX driver
(:mod:`app.domain.printing.cab_squix`), the workspace-scoped ``print_jobs``
ledger (:mod:`app.domain.printing.models`) and the transport/lifecycle service
(:mod:`app.domain.printing.print_service`). The label-template model, the
object-code resolver, the HTTP routes and the frontend editor are deliberately
NOT here — they land in follow-up PRs.
"""
