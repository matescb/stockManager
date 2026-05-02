"""Add BEFORE trigger to enforce parts.default_storage_location_id workspace.

Revision ID: 0036
Revises: 0035
Create Date: 2026-05-02

DB-015 Phase 2 / issue #254.

The service layer already rejects cross-workspace default_storage_location_id
assignments. This migration adds a Postgres BEFORE trigger that provides the
same guarantee at the database level, so that direct SQL (e.g. migrations,
admin queries) cannot silently produce an inconsistent row.

ERRCODE 23514 (check_violation) is raised so that SQLAlchemy surfaces it as
an IntegrityError — the same family as CHECK constraint failures.
"""

from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE OR REPLACE FUNCTION check_default_storage_workspace() RETURNS trigger AS $$
    BEGIN
      IF NEW.default_storage_location_id IS NOT NULL THEN
        PERFORM 1 FROM storage_locations
        WHERE id = NEW.default_storage_location_id
          AND workspace_id = NEW.workspace_id;
        IF NOT FOUND THEN
          RAISE EXCEPTION 'parts.default_storage_location_id (%) not in workspace (%)',
            NEW.default_storage_location_id, NEW.workspace_id
            USING ERRCODE = '23514';
        END IF;
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """)
    op.execute("""
    CREATE TRIGGER parts_default_storage_workspace_check
      BEFORE INSERT OR UPDATE OF default_storage_location_id, workspace_id
      ON parts
      FOR EACH ROW
      EXECUTE FUNCTION check_default_storage_workspace();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS parts_default_storage_workspace_check ON parts;")
    op.execute("DROP FUNCTION IF EXISTS check_default_storage_workspace();")
