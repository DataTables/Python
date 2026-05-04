"""
datatable.py – Read-only DataTables server-side processing wrapper.

:class:`DataTable` wraps :class:`~datatables_server.editor.Editor` with
``write=False`` and exposes a :class:`~datatables_server.column.Column`-oriented
API rather than a :class:`~datatables_server.field.Field`-oriented one.  It is
the correct entry point when you only need DataTables server-side processing
(SSP) without any Editor create / edit / delete support.

Typical usage::

    from datatables_server import Column
    from datatables_server.datatable import DataTable
    from sqlalchemy import create_engine

    engine = create_engine("sqlite:///mydb.db")

    with engine.connect() as conn:
        dt = (
            DataTable(conn, "users", "id")
            .columns(
                Column("first_name"),
                Column("last_name"),
                Column("email"),
            )
            .left_join("departments", "users.dept_id", "=", "departments.id")
        )
        dt.process(request_data)
        response = dt.data().to_dict()
"""

from __future__ import annotations

from typing import Any, Callable, List, Optional, Union

from sqlalchemy.engine import Connection

from .column import Column
from .editor import Editor
from .mjoin import Mjoin
from .types import DtRequest, DtResponse


class DataTable:
    """Read-only server-side processing wrapper for DataTables.

    :class:`DataTable` is a convenience class that builds on top of
    :class:`~datatables_server.editor.Editor` with write operations disabled
    (``write=False``).  It exposes a :class:`~datatables_server.column.Column`
    -oriented API so that callers work with column definitions rather than full
    :class:`~datatables_server.field.Field` instances.

    All methods that would normally return ``self`` on the underlying
    :class:`~datatables_server.editor.Editor` are proxied to return the
    :class:`DataTable` instance instead, preserving fluent chaining.

    Args:
        db:    SQLAlchemy database connection.
        table: Database table name(s) to read from.
        pkey:  Primary key column name(s).  Defaults to ``'id'``.

    Example::

        dt = DataTable(conn, "employees", "id")
        dt.columns(Column("name"), Column("salary"))
        dt.process(request_body)
        return dt.data().to_dict()
    """

    def __init__(
        self,
        db: Connection = None,
        table: Union[str, List[str]] = None,
        pkey: Union[str, List[str]] = None,
    ):
        """Create a new DataTable instance.

        Args:
            db:    SQLAlchemy :class:`~sqlalchemy.engine.Connection`.
            table: Database table name, or list of names.
            pkey:  Primary key column name(s).  Defaults to ``'id'``.
        """
        self._editor: Editor = Editor(db, table, pkey)
        self._editor.write(False)
        self._columns: List[Column] = []

    # ------------------------------------------------------------------
    # Column management
    # ------------------------------------------------------------------

    def column(self, name_or_column: Union[str, Column]) -> Union["DataTable", Column]:
        """Get a column by name, or add a single :class:`~datatables_server.column.Column`.

        When called with a string the method searches the registered columns and
        returns the matching :class:`~datatables_server.column.Column`, raising
        :exc:`KeyError` if none is found.

        When called with a :class:`~datatables_server.column.Column` instance
        the column is registered and ``self`` is returned for chaining.

        Args:
            name_or_column: Column name to look up, or a
                :class:`~datatables_server.column.Column` to add.

        Returns:
            The matching :class:`~datatables_server.column.Column` (getter) or
            ``self`` (setter / chaining).

        Raises:
            KeyError: If a string name is given and no matching column exists.
        """
        if isinstance(name_or_column, str):
            for col in self._columns:
                if col.name() == name_or_column:
                    return col
            raise KeyError(f"Unknown column: {name_or_column!r}")

        return self.columns(name_or_column)

    def columns(self, *cols: Union[Column, List[Column]]) -> Union["DataTable", List[Column]]:
        """Get all registered columns, or add one or more columns.

        Can be called with no arguments (getter), with individual
        :class:`~datatables_server.column.Column` instances, or with a list of
        them.

        Args:
            *cols: Zero or more :class:`~datatables_server.column.Column`
                instances (or lists of them) to register.

        Returns:
            List of :class:`~datatables_server.column.Column` instances
            (getter) or ``self`` (setter / chaining).
        """
        if not cols:
            return self._columns

        # Flatten – allow both ``columns(c1, c2)`` and ``columns([c1, c2])``
        flat: List[Column] = []
        for item in cols:
            if isinstance(item, list):
                flat.extend(item)
            else:
                flat.append(item)

        for col in flat:
            self._columns.append(col)
            self._editor.field(col.field())

        return self

    # ------------------------------------------------------------------
    # Proxied Editor methods
    # ------------------------------------------------------------------

    def data(self) -> DtResponse:
        """Return the :class:`~datatables_server.types.DtResponse` built by the last :meth:`process` call.

        Returns:
            The response object.  Call :meth:`~datatables_server.types.DtResponse.to_dict`
            on it to obtain a JSON-serialisable ``dict`` ready to send to the
            client.
        """
        return self._editor.data()

    def db(self, db: Connection = None) -> Union["DataTable", Connection]:
        """Get or set the database connection.

        Args:
            db: :class:`~sqlalchemy.engine.Connection` to assign.  Omit to use
                as a getter.

        Returns:
            The current connection (getter) or ``self`` (setter / chaining).
        """
        return self._proxy("db", [db])

    def debug(self, param: Any = None) -> Union["DataTable", bool]:
        """Get or set debug mode, or append a debug message.

        When called with no argument returns the current debug flag.  When
        called with ``True`` / ``False`` the flag is toggled.  Any other value
        is appended to the internal debug log and returned in the response
        when debug mode is active.

        Args:
            param: ``True`` / ``False`` to set the flag, any other value to
                log a message, or omit to read the current flag.

        Returns:
            Current flag value (getter) or ``self`` (setter / chaining).
        """
        return self._proxy("debug", [param])

    def id_prefix(self, id_prefix: str = None) -> Union["DataTable", str]:
        """Get or set the row-ID prefix prepended to every primary-key value.

        DataTables uses the DOM ``id`` attribute to track rows.  Because
        numeric primary keys are not valid HTML IDs on their own a prefix
        (default: ``'row_'``) is prepended to each value.

        Args:
            id_prefix: New prefix string.  Omit to use as a getter.

        Returns:
            Current prefix (getter) or ``self`` (setter / chaining).
        """
        return self._proxy("id_prefix", [id_prefix])

    def join(self, *join: Mjoin) -> Union["DataTable", List[Mjoin]]:
        """Get the configured :class:`~datatables_server.mjoin.Mjoin` instances, or add new ones.

        Args:
            *join: :class:`~datatables_server.mjoin.Mjoin` instances to add.
                Omit to use as a getter.

        Returns:
            List of :class:`~datatables_server.mjoin.Mjoin` instances (getter)
            or ``self`` (setter / chaining).
        """
        return self._proxy("join", list(join))

    def left_join(
        self,
        table: str,
        field1_or_fn: Union[str, Callable] = None,
        operator: str = None,
        field2: str = None,
    ) -> "DataTable":
        """Add a LEFT JOIN to every query executed by this instance.

        Two calling conventions are supported:

        * **Simple**: ``left_join("departments", "users.dept_id", "=", "departments.id")``
        * **Callable**: ``left_join("departments", lambda stmt: ...)``
          where the callable receives and returns a modified SQLAlchemy
          ``Select`` statement.

        Multiple calls accumulate; each adds another join.

        Args:
            table:         The table to join to.
            field1_or_fn:  Left-hand field name (e.g. ``'users.dept_id'``) or a
                           callable that modifies the statement.
            operator:      SQL comparison operator (e.g. ``'='``).
            field2:        Right-hand field name (e.g. ``'departments.id'``).

        Returns:
            ``self`` for method chaining.
        """
        return self._proxy("left_join", [table, field1_or_fn, operator, field2])

    def on(self, name: str, callback: Callable) -> "DataTable":
        """Add an event listener.

        Available events mirror those of :class:`~datatables_server.editor.Editor`:
        ``preGet``, ``postGet``, ``processed``, etc.

        Args:
            name:     Event name.
            callback: Callable invoked when the event fires.  The first
                argument is always the :class:`~datatables_server.editor.Editor`
                instance; subsequent arguments are event-specific.

        Returns:
            ``self`` for method chaining.
        """
        return self._proxy("on", [name, callback])

    def pkey(self, pkey: Union[str, List[str]] = None) -> Union["DataTable", List[str]]:
        """Get or set the primary key column name(s).

        Args:
            pkey: Single column name or list of names for a compound key.
                Omit to use as a getter.

        Returns:
            Current primary key list (getter) or ``self`` (setter / chaining).
        """
        return self._proxy("pkey", [pkey])

    def process(self, data: Union[DtRequest, dict], files: dict = None) -> "DataTable":
        """Process a DataTables server-side processing request.

        Parses *data*, executes the appropriate database query, and stores the
        result internally.  Retrieve the result afterwards with :meth:`data`.

        Because :class:`DataTable` sets ``write=False`` on the underlying
        :class:`~datatables_server.editor.Editor`, create / edit / delete
        actions are silently ignored even if they appear in *data*.

        Args:
            data:  Request body as a :class:`~datatables_server.types.DtRequest`
                   or a raw ``dict`` (e.g. ``request.json``).
            files: Unused for :class:`DataTable`; present for API compatibility
                   with :class:`~datatables_server.editor.Editor`.

        Returns:
            ``self`` for method chaining.
        """
        self._editor.process(data, files)
        return self

    def read_table(self, table: Union[str, List[str]] = None) -> Union["DataTable", List[str]]:
        """Get or set a separate table name used only for read (SELECT) operations.

        This is useful when you want to read from a VIEW (which may use a
        complex SELECT internally) while writes go to a different base table.

        Args:
            table: Table name or list of names for reads.  Omit to use as a
                getter.

        Returns:
            Current read-table list (getter) or ``self`` (setter / chaining).
        """
        return self._proxy("read_table", [table])

    def schema(self, schema: str = None) -> Union["DataTable", str]:
        """Get or set the database schema used to qualify table names.

        Args:
            schema: Schema name (e.g. ``'public'``).  Omit to use as a getter.

        Returns:
            Current schema string (getter) or ``self`` (setter / chaining).
        """
        return self._proxy("schema", [schema])

    def table(self, table: Union[str, List[str]] = None) -> Union["DataTable", List[str]]:
        """Get or set the database table name(s).

        Table names may include an alias using SQL syntax, e.g.
        ``'users as u'``.

        Args:
            table: Table name or list of names.  Omit to use as a getter.

        Returns:
            Current table list (getter) or ``self`` (setter / chaining).
        """
        return self._proxy("table", [table])

    def try_catch(self, try_catch: bool = None) -> Union["DataTable", bool]:
        """Get or set whether exceptions are caught and returned as error responses.

        When ``True``, any unhandled exception raised during :meth:`process` is
        caught and its message is placed in the ``error`` field of the response
        instead of propagating.  Useful in production; disable for debugging.

        Args:
            try_catch: New flag value.  Omit to use as a getter.

        Returns:
            Current flag value (getter) or ``self`` (setter / chaining).
        """
        return self._proxy("try_catch", [try_catch])

    def where(self, *cond: Any) -> Union["DataTable", List[Any]]:
        """Get or add WHERE conditions applied to every query.

        Conditions can be:

        * A SQLAlchemy clause element (e.g. ``sa.column('active') == 1``).
        * A callable ``lambda stmt: stmt.where(...)`` that receives and returns
          a modified SQLAlchemy ``Select`` statement.

        Multiple calls accumulate; all conditions are combined with AND.

        Args:
            *cond: Zero or more conditions.  Omit to use as a getter.

        Returns:
            Current condition list (getter) or ``self`` (setter / chaining).
        """
        return self._proxy("where", list(cond))

    def where_clear(self) -> "DataTable":
        """Remove all WHERE conditions added via :meth:`where`.

        Returns:
            ``self`` for method chaining.
        """
        self._editor.where_clear()
        return self

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _proxy(self, method: str, args: list = None) -> Any:
        """Call *method* on the underlying :class:`~datatables_server.editor.Editor`.

        If the editor returns itself (indicating a setter / chaining call)
        this method returns ``self`` (the :class:`DataTable` instance) so that
        callers can chain against :class:`DataTable` rather than the inner
        :class:`~datatables_server.editor.Editor`.

        Args:
            method: Name of the :class:`~datatables_server.editor.Editor`
                method to call.
            args:   Positional arguments to pass.  ``None`` values are filtered
                out so that getter overloads (no-argument forms) are triggered
                correctly.

        Returns:
            The editor's return value, or ``self`` when the editor would have
            returned itself.
        """
        if args is None:
            args = []

        fn = getattr(self._editor, method)

        # Strip trailing None args so zero-argument getter overloads fire.
        while args and args[-1] is None:
            args = args[:-1]

        ret = fn(*args)

        if ret is self._editor:
            return self

        return ret
