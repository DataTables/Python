"""
The :class:`StateRestore` class provides server-side storage for the DataTables
StateRestore extension, allowing saved states to be shared between browsers and
users.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Union

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from .editor import parse_form_data

# ---------------------------------------------------------------------------
# Public type aliases
# ---------------------------------------------------------------------------

StateRestoreSubmit = Mapping[str, Any]
"""Data submitted by the StateRestore client.

Recognised keys (all optional unless noted, camelCase as sent by the client):

* ``'action'``       - required; one of ``'state-read'``, ``'state-create'``,
                       ``'state-edit'`` or ``'state-remove'``.
* ``'id'``           - id of the state being edited.
* ``'ids'``          - list of state ids to remove.
* ``'isDefault'``    - flag for the default state.
* ``'isSharedOut'``  - flag for a state shared with other users.
* ``'name'``         - name of the state.
* ``'path'``         - URL (path) of the page the state applies to.
* ``'state'``        - the state itself, as a JSON string.
* ``'table'``        - name of the host DataTable.
"""

State = Dict[str, Any]
"""A single state as returned to the StateRestore client."""

StateRestoreResponse = Dict[str, Any]
"""Response for a StateRestore request - an ``'error'`` string or a
``'data'`` list of :data:`State` dicts."""


class StateRestore:
    """Server-side storage of DataTables StateRestore states.

    This is a port of the TypeScript ``StateRestore`` class from
    ``stateRestore.ts``.  Instances are configured with the chainable API and
    then handed the submitted request data via :meth:`process`, with the
    response read back from :meth:`data`.

    Typical usage::

        state = StateRestore(conn, "states").user(session_user_id)
        state.process(request_data)
        response = state.data()

    The states are stored in a single database table, with one column per
    property of the state.  The column names are configurable via the
    ``column_*`` methods.
    """

    def __init__(
        self,
        db: Connection = None,
        table: str = None,
        pkey: str = None,
    ) -> None:
        """Create a StateRestore instance.

        Args:
            db:    SQLAlchemy :class:`~sqlalchemy.engine.Connection`.
            table: Database table name for where the states will be stored.
            pkey:  Primary key column name for that table.  Defaults to ``'id'``.
        """
        self._column_default: str = "defaultState"
        self._column_id: str = "id"
        self._column_name: str = "name"
        self._column_path: str = "path"
        self._column_shared: str = "shared"
        self._column_state: str = "state"
        self._column_table: str = "table"
        self._column_user: str = "user"
        self._db: Optional[Connection] = None
        self._result: StateRestoreResponse = {}
        self._set: Dict[str, Any] = {}
        self._table: str = ""
        self._user_id: str = ""
        self._where: List[Any] = []

        if db is not None:
            self.db(db)
        if table is not None:
            self.table(table)
        if pkey is not None:
            self.column_id(pkey)

    # ------------------------------------------------------------------
    # Public chainable API
    # ------------------------------------------------------------------

    def column_default(self, column: str = None) -> Union["StateRestore", str]:
        """Get or set the column name for the default state flag.

        Args:
            column: Column name.  Omit to use as a getter.

        Returns:
            The current column name (getter) or ``self`` (setter / chaining).
        """
        if column is None:
            return self._column_default

        self._column_default = column
        return self

    def column_id(self, column: str = None) -> Union["StateRestore", str]:
        """Get or set the column name for the table's primary key.

        Args:
            column: Column name.  Omit to use as a getter.

        Returns:
            The current column name (getter) or ``self`` (setter / chaining).
        """
        if column is None:
            return self._column_id

        self._column_id = column
        return self

    def column_name(self, column: str = None) -> Union["StateRestore", str]:
        """Get or set the column name for the state's name.

        Args:
            column: Column name.  Omit to use as a getter.

        Returns:
            The current column name (getter) or ``self`` (setter / chaining).
        """
        if column is None:
            return self._column_name

        self._column_name = column
        return self

    def column_path(self, column: str = None) -> Union["StateRestore", str]:
        """Get or set the column name for the URL (path) where the state applies.

        Args:
            column: Column name.  Omit to use as a getter.

        Returns:
            The current column name (getter) or ``self`` (setter / chaining).
        """
        if column is None:
            return self._column_path

        self._column_path = column
        return self

    def column_shared(self, column: str = None) -> Union["StateRestore", str]:
        """Get or set the column name for the shared flag.

        Args:
            column: Column name.  Omit to use as a getter.

        Returns:
            The current column name (getter) or ``self`` (setter / chaining).
        """
        if column is None:
            return self._column_shared

        self._column_shared = column
        return self

    def column_state(self, column: str = None) -> Union["StateRestore", str]:
        """Get or set the column name for where the state itself is stored.

        Args:
            column: Column name.  Omit to use as a getter.

        Returns:
            The current column name (getter) or ``self`` (setter / chaining).
        """
        if column is None:
            return self._column_state

        self._column_state = column
        return self

    def column_table(self, column: str = None) -> Union["StateRestore", str]:
        """Get or set the column name for where the host DataTable name is stored.

        Args:
            column: Column name.  Omit to use as a getter.

        Returns:
            The current column name (getter) or ``self`` (setter / chaining).
        """
        if column is None:
            return self._column_table

        self._column_table = column
        return self

    def column_user(self, column: str = None) -> Union["StateRestore", str]:
        """Get or set the column name for where the user identifier is stored.

        Args:
            column: Column name.  Omit to use as a getter.

        Returns:
            The current column name (getter) or ``self`` (setter / chaining).
        """
        if column is None:
            return self._column_user

        self._column_user = column
        return self

    def data(self) -> StateRestoreResponse:
        """Return the data constructed and resulting from this instance being processed.

        Call :meth:`process` first to populate the response.

        Returns:
            The result - a dict with either an ``'error'`` or ``'data'`` key.
        """
        return self._result

    def db(self, db: Connection = None) -> Union["StateRestore", Connection]:
        """Get or set the database connection.

        Args:
            db: SQLAlchemy :class:`~sqlalchemy.engine.Connection`.
                Omit to use as a getter.

        Returns:
            The current connection (getter) or ``self`` (setter / chaining).
        """
        if db is None:
            return self._db

        self._db = db
        return self

    def process(self, data: StateRestoreSubmit) -> "StateRestore":
        """Process a request from the StateRestore client.

        The submitted ``'action'`` determines the operation performed - reading,
        creating, editing or removing states.  The result is available from
        :meth:`data`.

        Bracket notation submitted by the client (``ids[]=1&ids[]=2``) is
        resolved with :func:`~datatables_server.parse_form_data`, so the raw
        form data from the web framework can be passed in directly.

        Args:
            data: The submitted request data.

        Returns:
            ``self`` for method chaining.
        """
        if data and (not isinstance(data, dict) or any("[" in key for key in data)):
            data = parse_form_data(data)

        action = data.get("action") if data else None

        if action is None:
            self._result = {"error": "Unknown action"}
        elif action == "state-read":
            self._result = self._read(data)
        elif action == "state-create":
            self._result = self._create(data)
        elif action == "state-edit":
            self._result = self._edit(data)
        elif action == "state-remove":
            self._result = self._remove(data)

        return self

    def set(self, set_values: Dict[str, Any] = None) -> Union["StateRestore", Dict[str, Any]]:
        """Get or set extra column name / value properties to store in the db.

        The values given are written as part of the state on create and edit.
        Note that they are not read back from the db as part of loading the
        state list.

        Args:
            set_values: Column name / value pairs.  Omit to use as a getter.

        Returns:
            The current values (getter) or ``self`` (setter / chaining).
        """
        if set_values is None:
            return self._set

        self._set = set_values
        return self

    def table(self, name: str = None) -> Union["StateRestore", str]:
        """Get or set the database table name used for state storage.

        Args:
            name: Table name.  Omit to use as a getter.

        Returns:
            The current table name (getter) or ``self`` (setter / chaining).
        """
        if name is None:
            return self._table

        self._table = name
        return self

    def user(self, name: str = None) -> Union["StateRestore", str]:
        """Get or set the value for the current user identifier.

        This is usually a user id, but it could be any other unique identifier.
        When not set there is no separation of states between users.

        Args:
            name: User ID.  Omit to use as a getter.

        Returns:
            The current user identifier (getter) or ``self`` (setter / chaining).
        """
        if name is None:
            return self._user_id

        self._user_id = name
        return self

    def where(self, *cond: Any) -> Union["StateRestore", List[Any]]:
        """Get the current WHERE conditions, or append one or more.

        Conditions can be either:

        * SQLAlchemy clause elements (e.g. ``sa.column('x') == 1``)
        * Callables that accept the statement and return a modified statement
          (e.g. ``lambda s: s.where(sa.column('x') == 1)``)

        They are applied to the SELECT used to read the state list.

        Args:
            *cond: Zero or more WHERE conditions.

        Returns:
            Current condition list (getter) or ``self`` (setter / chaining).
        """
        if not cond:
            return self._where

        self._where.extend(cond)
        return self

    # ------------------------------------------------------------------
    # Private methods
    # ------------------------------------------------------------------

    def _assert(self, data: StateRestoreSubmit, prop: str) -> bool:
        """Check that a property was submitted.

        Args:
            data: Data to validate.
            prop: Property to check for.

        Returns:
            ``True`` if the property was submitted.
        """
        return bool(data) and prop in data

    def _assert_state_data(self, data: StateRestoreSubmit) -> Optional[StateRestoreResponse]:
        """Validate submitted state data.

        Args:
            data: Data to validate.

        Returns:
            ``None`` if the data is valid, otherwise an error response.
        """
        if not self._assert(data, "isDefault"):
            return {"error": "Incomplete data - no default flag"}

        if not self._assert(data, "isSharedOut"):
            return {"error": "Incomplete data - no share flag"}

        if not self._assert(data, "name") or not data.get("name"):
            return {"error": "Incomplete data - no name"}

        if not self._assert(data, "state"):
            return {"error": "Incomplete data - no valid state"}

        return self._assert_state_host(data)

    def _assert_state_host(self, data: StateRestoreSubmit) -> Optional[StateRestoreResponse]:
        """Check the parameters that are submitted for host table information.

        Args:
            data: Data to validate.

        Returns:
            ``None`` if the data is valid, otherwise an error response.
        """
        if not self._assert(data, "path") or not data.get("path"):
            return {"error": "Incomplete data - no path"}

        if not self._assert(data, "table") or not data.get("table"):
            return {"error": "Incomplete data - table"}

        return None

    def _create(self, data: StateRestoreSubmit) -> StateRestoreResponse:
        """Add a new state to the database.

        Args:
            data: State information.

        Returns:
            The state list for the host table, limited to the new state.
        """
        validated = self._assert_state_data(data)

        if validated is not None:
            return validated

        # Values to set
        values: Dict[str, Any] = {
            self._column_default: self._value_boolean(data.get("isDefault")),
            self._column_name: data.get("name"),
            self._column_path: data.get("path"),
            self._column_shared: self._value_boolean(data.get("isSharedOut")),
            self._column_state: data.get("state"),
            self._column_table: data.get("table"),
        }

        if self._user_id:
            values[self._column_user] = self._user_id

        # Dev defined values (server-side)
        values.update(self._set)

        # There can be only one
        if values[self._column_default]:
            self._remove_default(data)

        tbl = sa.table(self._table, *[sa.column(c) for c in values])
        stmt = sa.insert(tbl).values(values)

        # Use RETURNING when the dialect supports it (PostgreSQL, SQLite >= 3.35);
        # fall back to lastrowid for dialects that don't (MySQL/MariaDB).
        if getattr(self._db.dialect, "insert_returning", False):
            row = self._db.execute(stmt.returning(sa.column(self._column_id))).fetchone()
            id = str(row[0]) if row else None
        else:
            result = self._db.execute(stmt)
            id = str(result.lastrowid) if result.lastrowid else None

        self._db.commit()

        return self._read(data, id)

    def _edit(self, data: StateRestoreSubmit) -> StateRestoreResponse:
        """Update a state on the database.

        Args:
            data: State information.

        Returns:
            The state list for the host table, limited to the edited state.
        """
        validated = self._assert_state_data(data)

        if validated is not None:
            return validated

        if not data.get("id"):
            return {"error": "Incomplete data - no id"}

        # Values to set
        values: Dict[str, Any] = {
            self._column_default: self._value_boolean(data.get("isDefault")),
            self._column_name: data.get("name"),
            self._column_shared: self._value_boolean(data.get("isSharedOut")),
            self._column_state: data.get("state"),
        }

        # Dev defined values (server-side)
        values.update(self._set)

        # Conditions
        where: Dict[str, Any] = {
            self._column_id: data.get("id"),
            self._column_table: data.get("table"),
            self._column_path: data.get("path"),
        }

        if self._user_id:
            where[self._column_user] = self._user_id

        # There can be only one
        if values[self._column_default]:
            self._remove_default(data)

        self._update(values, where)
        self._db.commit()

        return self._read(data, data.get("id"))

    def _read(
        self,
        data: StateRestoreSubmit,
        id: Union[str, int] = None,
    ) -> StateRestoreResponse:
        """Read the states from the db.

        Args:
            data: Submitted data.
            id:   Limit the read to a specific ID.

        Returns:
            Read data, in the structure that StateRestore expects.
        """
        # Must have the table and path, otherwise all states would be returned!
        validated = self._assert_state_host(data)

        if validated is not None:
            return validated

        cols = [sa.column(self._column_id).label("id")]

        if self._column_default:
            cols.append(sa.column(self._column_default).label("isDefault"))

        if self._column_name:
            cols.append(sa.column(self._column_name).label("name"))

        if self._column_shared:
            cols.append(sa.column(self._column_shared).label("isSharedOut"))

        if self._column_state:
            cols.append(sa.column(self._column_state).label("state"))

        if self._column_user:
            cols.append(sa.column(self._column_user).label("user"))

        stmt = (
            sa.select(*cols)
            .select_from(sa.text(self._table))
            .where(sa.column(self._column_table) == data.get("table"))
            .where(sa.column(self._column_path) == data.get("path"))
        )

        if id:
            stmt = stmt.where(sa.column(self._column_id) == id)

        # The user id is optional, but there can't be any separation of user
        # states without it!
        if self._user_id:
            stmt = stmt.where(
                sa.or_(
                    sa.column(self._column_user) == self._user_id,
                    sa.column(self._column_shared) == 1,
                )
            )

        # Dev set conditions
        for cond in self._where:
            if callable(cond):
                stmt = cond(stmt)
            else:
                stmt = stmt.where(cond)

        # Run the assembled query and map to the JSON structure that
        # StateRestore expects
        rows = [dict(row._mapping) for row in self._db.execute(stmt)]

        out: List[State] = [
            {
                "id": row.get("id"),
                "isDefault": row.get("isDefault"),
                "isSharedIn": bool(self._user_id) and str(row.get("user")) != str(self._user_id),
                "isSharedOut": row.get("isSharedOut"),
                "isStatic": False,
                "name": row.get("name"),
                "state": row.get("state"),
            }
            for row in rows
        ]

        return {"data": out}

    def _remove(self, data: StateRestoreSubmit) -> StateRestoreResponse:
        """Delete states.

        Args:
            data: Submitted data with an ``'ids'`` parameter - a list of ids, or
                a single id.

        Returns:
            An empty data list, or an error response.
        """
        validated = self._assert_state_host(data)

        if validated is not None:
            return validated

        ids = data.get("ids")

        if isinstance(ids, (str, int)):
            ids = [ids]

        if not ids or not isinstance(ids, (list, tuple)):
            return {"error": "Invalid submitted data"}

        cols = [self._column_id, self._column_table, self._column_path]

        if self._user_id:
            cols.append(self._column_user)

        tbl = sa.table(self._table, *[sa.column(c) for c in cols])
        stmt = (
            sa.delete(tbl)
            .where(sa.column(self._column_table) == data.get("table"))
            .where(sa.column(self._column_path) == data.get("path"))
            .where(sa.column(self._column_id).in_(list(ids)))
        )

        if self._user_id:
            stmt = stmt.where(sa.column(self._column_user) == self._user_id)

        self._db.execute(stmt)
        self._db.commit()

        return {"data": []}

    def _remove_default(self, data: StateRestoreSubmit) -> None:
        """Remove any existing default state.

        The client-side will do this as well, so we don't need to worry about
        there being two default states shown, despite only returning a single
        record.

        Args:
            data: Submitted data.
        """
        if self._assert_state_host(data) is not None:
            return

        values = {self._column_default: 0}

        # Conditions
        where: Dict[str, Any] = {
            self._column_default: 1,
            self._column_table: data.get("table"),
            self._column_path: data.get("path"),
        }

        if self._user_id:
            where[self._column_user] = self._user_id

        self._update(values, where)

    def _update(self, values: Dict[str, Any], where: Dict[str, Any]) -> None:
        """Run an UPDATE against the state table.

        Args:
            values: Column name / value pairs to write.
            where:  Column name / value pairs to match on (joined with ``AND``).
        """
        cols = list(values) + [c for c in where if c not in values]
        tbl = sa.table(self._table, *[sa.column(c) for c in cols])
        conds = [sa.column(col) == val for col, val in where.items()]

        self._db.execute(sa.update(tbl).where(sa.and_(*conds)).values(values))

    def _value_boolean(self, data: Any) -> int:
        """Convert a boolean HTTP value to a database integer flag.

        Args:
            data: Data to check.

        Returns:
            ``1`` for a truthy submitted value, ``0`` otherwise.
        """
        if data in ("true", "t", "1", 1, True):
            return 1

        return 0
