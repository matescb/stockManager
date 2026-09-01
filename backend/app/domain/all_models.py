"""Single import to ensure every SQLAlchemy model is registered with Base.metadata.

CONTRACT — every domain `models.py` module must be imported here.

The Alembic environment script imports this module so that
`Base.metadata` lists every table at autogenerate time. A model module
that exists on disk but is missing from this barrel does NOT fail
loudly — `alembic revision --autogenerate` just silently omits the
table from the migration, and the model only fails at the moment a
test or runtime call tries to use it.

`tests/test_migrations.py::test_all_models_covers_every_domain` is the
regression net: it walks `app/domain/*/models.py`, imports each
module, collects every class with a `__tablename__`, and asserts each
tablename exists in `Base.metadata.tables` after THIS module is
imported. A new domain models module that's not added below makes that
test fail with a message naming the offending model and module.

When adding a new model, do both of:
  1. Append the import here (alpha-order by package).
  2. Generate the migration with `alembic revision --autogenerate`.
The test in step (1) does not replace step (2) — it only catches the
case where step (1) was forgotten.
"""

from app.domain._polymorphic_cleanup import register_polymorphic_cleanup_listeners
from app.domain.attachments.models import Attachment  # noqa: F401
from app.domain.audit.models import AuditLog  # noqa: F401
from app.domain.builds.models import Build  # noqa: F401
from app.domain.categories.models import PartCategory  # noqa: F401
from app.domain.custom_fields.models import CustomField  # noqa: F401
from app.domain.eda.models import (  # noqa: F401
    EdaDatafile,
    EdaFootprint,
    EdaFootprintModel,
    EdaSymbol,
    PartEda,
)
from app.domain.fx.models import FxRateSnapshot  # noqa: F401
from app.domain.lots.models import Lot  # noqa: F401
from app.domain.orders.models import Order, OrderEntry  # noqa: F401
from app.domain.parts.models import (  # noqa: F401
    BulkImportIdempotency,
    Part,
    PartCadKey,
    PartMetaMember,
    PartSubstitute,
)
from app.domain.projects.models import (  # noqa: F401
    BomImportPreset,
    Project,
    ProjectEntry,
)
from app.domain.sourcing.models import (  # noqa: F401
    PurchasePlan,
    PurchasePlanLine,
    SourcingAlert,
    SourcingCache,
)
from app.domain.stock.models import StockEntry  # noqa: F401
from app.domain.storage.models import StorageLocation  # noqa: F401
from app.domain.tags.models import Tag, TagLink  # noqa: F401
from app.domain.users.models import (  # noqa: F401
    PasswordResetRequest,
    PendingUser,
    User,
    UserLoginFailure,
    UserSession,
)
from app.domain.workspaces.models import (  # noqa: F401
    Workspace,
    WorkspaceCatalogToken,
    WorkspaceInvitation,
    WorkspaceMember,
)

register_polymorphic_cleanup_listeners()
