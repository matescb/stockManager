"""Use a custom SQLSTATE for workspace-isolation trigger failures.

Revision ID: 0060
Revises: 0059
Create Date: 2026-05-15

AUD-079 / issue #717.

Superseded by 0064, which recreates these trigger functions from inline
definitions instead of reading their current database definitions.

The trigger bodies were last updated in 0058 to preserve the #710 update-gating
semantics. Rewrite only the ERRCODE inside those current function definitions
so this migration does not drift from that trigger logic.
"""

from __future__ import annotations

from alembic import op

revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None


_WORKSPACE_TRIGGER_FUNCTIONS = (
    "check_stock_entries_workspace_fks()",
    "check_part_substitutes_workspace_fks()",
    "check_part_meta_members_workspace_fks()",
    "check_part_cad_keys_workspace()",
)


def _rewrite_sqlstate(from_sqlstate: str, to_sqlstate: str) -> None:
    for function_signature in _WORKSPACE_TRIGGER_FUNCTIONS:
        op.execute(
            f"""
            DO $$
            DECLARE
              function_def text;
              rewritten text;
            BEGIN
              SELECT pg_get_functiondef('{function_signature}'::regprocedure)
                INTO function_def;

              rewritten := replace(
                function_def,
                $needle$ERRCODE = '{from_sqlstate}'$needle$,
                $replacement$ERRCODE = '{to_sqlstate}'$replacement$
              );

              IF rewritten = function_def THEN
                RAISE EXCEPTION
                  'function {function_signature} did not contain SQLSTATE {from_sqlstate}';
              END IF;

              EXECUTE rewritten;
            END
            $$;
            """
        )


def upgrade() -> None:
    _rewrite_sqlstate("23514", "WS001")


def downgrade() -> None:
    _rewrite_sqlstate("WS001", "23514")
