from __future__ import annotations

import pathlib

DOCS_ROOT = pathlib.Path(__file__).parents[2] / "docs"


def test_password_pepper_rotation_requires_password_reset_campaign() -> None:
    text = (DOCS_ROOT / "runbooks" / "secret-rotation.md").read_text(
        encoding="utf-8"
    )

    assert "### 2.2 `PASSWORD_PEPPER`" in text
    assert "planned password-reset campaign" in text
    assert "Force a password reset for all users before replacing the value." in text
