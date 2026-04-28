"""Single import to ensure every SQLAlchemy model is registered with Base.metadata."""

from app.domain.attachments.models import Attachment  # noqa: F401
from app.domain.custom_fields.models import CustomField  # noqa: F401
from app.domain.lots.models import Lot  # noqa: F401
from app.domain.orders.models import Order, OrderEntry  # noqa: F401
from app.domain.parts.models import (  # noqa: F401
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
from app.domain.stock.models import StockEntry  # noqa: F401
from app.domain.storage.models import StorageLocation  # noqa: F401
from app.domain.tags.models import Tag, TagLink  # noqa: F401
from app.domain.users.models import User, UserSession  # noqa: F401
from app.domain.workspaces.models import Workspace, WorkspaceMember  # noqa: F401
