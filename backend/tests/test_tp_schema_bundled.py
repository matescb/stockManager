from __future__ import annotations

import importlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC_PATH = ROOT / "docs" / "schemas" / "trustedparts-v2.json"
GENERATED_PATH = (
    ROOT / "backend" / "app" / "domain" / "sourcing" / "_generated" / "trustedparts_v2.py"
)
GENERATED_HEADER = (
    "# AUTO-GENERATED FILE - DO NOT EDIT. "
    "Run `make regen-tp-models` from the repository root."
)


def test_bundled_trustedparts_schema_loads() -> None:
    schema = json.loads(SPEC_PATH.read_text())

    assert schema["openapi"] == "3.0.4"
    assert schema["info"]["title"] == "TrustedParts.com Inventory API"
    assert schema["info"]["version"] == "2.0"
    assert schema["paths"]["/v2/search"]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"]["$ref"] == "#/components/schemas/InventoryApiRequest"


def test_generated_trustedparts_module_imports_and_has_header() -> None:
    first_line = GENERATED_PATH.read_text().splitlines()[0]
    assert first_line == GENERATED_HEADER

    module = importlib.import_module("app.domain.sourcing._generated.trustedparts_v2")

    assert module.InventoryApiRequest.model_fields["Queries"].is_required()
    assert "PartResults" in module.InventoryApiResponse.model_fields
