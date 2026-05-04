"""
Validation host container – holds context information during field validation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

    from .editor import Editor
    from .field import Field


class ValidationHost:
    """Container for field and editor context during validation.

    An instance of this class is created for every field being validated and is
    passed to every validator function so that validators can inspect the broader
    request context (e.g. inspect other submitted field values or perform their
    own database queries).

    Attributes:
        action: The Editor action being performed.  One of ``'create'``,
            ``'edit'``, or ``'remove'``.
        id:     The id of the row being edited or removed.  ``None`` /  empty
            string for create operations.
        field:  The :class:`~datatables_server.Field` instance being validated.
        editor: The :class:`~datatables_server.Editor` instance processing the
            request.
        db:     The active SQLAlchemy :class:`~sqlalchemy.engine.Connection`.
    """

    def __init__(
        self,
        action: str,
        id: str,
        field: "Field",
        editor: "Editor",
        db: "Connection",
    ) -> None:
        """Initialise the validation host with all required context.

        Args:
            action: Editor action – ``'create'``, ``'edit'``, or ``'remove'``.
            id:     Row id being processed (empty string for create).
            field:  The :class:`~datatables_server.Field` being validated.
            editor: The owning :class:`~datatables_server.Editor` instance.
            db:     Active SQLAlchemy database connection.
        """
        self.action: str = action
        self.id: str = id
        self.field: "Field" = field
        self.editor: "Editor" = editor
        self.db: "Connection" = db
