"""
Main DataTables Editor server-side processing class.
"""

from __future__ import annotations

import binascii
import re
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Union

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from .columncontrol import column_control_ssp
from .field import Field, SetType
from .mjoin import Mjoin
from .nested_data import NestedData
from .types import DtColumn, DtColumnControl, DtError, DtOrder, DtRequest, DtResponse, LeftJoin, SspResult

# ---------------------------------------------------------------------------
# Public type aliases
# ---------------------------------------------------------------------------

GlobalValidator = Callable[["Editor", str, DtRequest], Union[bool, str]]
"""Callable signature for global validators.

Receives the :class:`Editor` instance, the action string, and the parsed
:class:`~datatables_server.types.DtRequest`.  Return ``True`` to pass, or a
human-readable error string to fail.
"""

GetFn = Callable[[Optional[Union[str, List[str]]], Optional[DtRequest]], DtResponse]
"""Callable signature for a custom GET function registered via :meth:`Editor.get`."""


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _apply_left_joins(stmt: sa.Select, left_joins: List[LeftJoin]) -> sa.Select:
    """Append LEFT JOIN clauses to *stmt*.

    For each :class:`~datatables_server.types.LeftJoin`:

    * If ``fn`` is set the callable receives the statement and must return the
      modified statement.
    * Otherwise a raw ``LEFT JOIN … ON field1 operator field2`` clause is
      appended using SQLAlchemy's outer-join facility.

    Args:
        stmt:       The SELECT statement to extend.
        left_joins: Ordered list of join descriptors.

    Returns:
        The (possibly modified) SELECT statement.
    """
    for lj in left_joins:
        if lj.fn:
            stmt = lj.fn(stmt)
        else:
            on_clause = sa.text(f"{lj.field1} {lj.operator} {lj.field2}")
            stmt = stmt.join(sa.text(lj.table), on_clause, isouter=True)
    return stmt


# ColumnControl SSP filtering is delegated to columncontrol.py.
# The public ``column_control_ssp`` function imported above is called directly
# from ``_ssp_filter``; no local wrappers are needed here.


def parse_form_data(data: Any) -> dict:
    """Convert a raw HTTP form submission into a plain nested ``dict``.

    DataTables and Editor submit form data using PHP/jQuery bracket notation,
    e.g.::

        data[row_58][first_name] = John
        data[row_58][last_name]  = Doe
        action                   = edit

    Standard ``request.form.to_dict()`` flattens this into literal key strings
    rather than a nested structure.  This function parses those bracket-encoded
    keys back into a proper nested ``dict`` that :meth:`Editor.process` can
    consume.

    The function also accepts objects that expose a ``to_dict(flat=False)``
    method (such as Werkzeug's ``ImmutableMultiDict``), a ``multi_items()``
    iterable, a plain ``dict``, or any mapping — making it safe to call
    regardless of what the web framework hands you.

    Args:
        data: Raw form data from the web framework — a ``dict``, a Werkzeug
            ``ImmutableMultiDict``, or any mapping.

    Returns:
        A plain nested ``dict`` with bracket notation fully resolved.
    """
    # If it's already a plain dict with a proper 'data' key containing a dict,
    # assume it has already been decoded (e.g. JSON body) and return as-is.
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        return data

    # Collect flat key/value pairs from whatever object we received.
    pairs: List[tuple] = []

    if hasattr(data, "multi_items"):
        # Werkzeug ImmutableMultiDict (Flask request.form)
        pairs = list(data.multi_items())
    elif hasattr(data, "items"):
        # Plain dict or dict-like mapping
        for k, v in data.items():
            if isinstance(v, list):
                for item in v:
                    pairs.append((k, item))
            else:
                pairs.append((k, v))
    else:
        return dict(data) if data else {}

    result: dict = {}

    for raw_key, value in pairs:
        # Split 'a[b][c]' into ['a', 'b', 'c']
        parts = [p.rstrip("]") for p in raw_key.replace("[", "]").split("]") if p != ""]

        node = result
        for part in parts[:-1]:
            if part not in node:
                node[part] = {}
            elif not isinstance(node[part], dict):
                # Already a scalar — promote to dict (shouldn't normally happen)
                node[part] = {}
            node = node[part]

        leaf = parts[-1]
        if leaf in node and isinstance(node[leaf], dict):
            # Don't overwrite a dict that child keys already populated
            pass
        else:
            node[leaf] = value

    # Promote integer-indexed sub-dicts to lists everywhere EXCEPT the top-level
    # "data" dict, whose keys are row identifiers ("row_42" for edit, "0" / "1"
    # for create).  Create indices look like array indices but are not — they are
    # positional submit keys used as temporary row handles, not ordered list items.
    if "data" in result and isinstance(result["data"], dict):
        result["data"] = {row_key: _promote_arrays(row_val) for row_key, row_val in result["data"].items()}
        # Promote everything else at the top level (columns, order, etc.)
        result = {k: (_promote_arrays(v) if k != "data" else v) for k, v in result.items()}
    else:
        result = _promote_arrays(result)

    return result


def _promote_arrays(obj: Any) -> Any:
    """Recursively convert index-keyed dicts to lists.

    jQuery / PHP bracket notation encodes arrays as consecutive integer keys:
    ``name[0]=a&name[1]=b`` parses to ``{"name": {"0": "a", "1": "b"}}``.  This
    function converts any ``dict`` whose keys are the consecutive integers
    ``"0", "1", …, "n-1"`` into a ``list``, which is what the rest of the
    library expects for Mjoin array fields.

    Dicts whose keys are *not* a complete consecutive integer sequence (e.g.
    row IDs like ``"row_42"`` or field names like ``"first_name"``) are left
    unchanged.

    Args:
        obj: Any value — recursed into for dicts.

    Returns:
        The same value with integer-indexed sub-dicts promoted to lists.
    """
    if not isinstance(obj, dict):
        return obj

    # Recurse first so nested arrays are also promoted
    obj = {k: _promote_arrays(v) for k, v in obj.items()}

    # Promote to list when every key is a consecutive integer string "0"…"n-1"
    keys = list(obj.keys())
    if keys and all(k.isdigit() for k in keys):
        indices = sorted(int(k) for k in keys)
        if indices == list(range(len(indices))):
            return [obj[str(i)] for i in indices]

    return obj


# ---------------------------------------------------------------------------
# Action enum
# ---------------------------------------------------------------------------


class Action(Enum):
    """Actions that can be requested by the client-side DataTables / Editor."""

    READ = "read"
    CREATE = "create"
    EDIT = "edit"
    DELETE = "delete"
    UPLOAD = "upload"
    SEARCH = "search"


# ---------------------------------------------------------------------------
# Editor class
# ---------------------------------------------------------------------------


class Editor(NestedData):
    """DataTables Editor server-side processing class.

    Handles all CRUD operations for DataTables Editor, managing database read
    and write operations using synchronous SQLAlchemy Core.

    The class is designed to be used per-request: create an instance (or reuse
    a configured one), call :meth:`process` with the incoming HTTP body, and
    serialise :meth:`data` back to the client as JSON.

    Example::

        from sqlalchemy import create_engine
        from datatables_server import Editor, Field, Validate

        engine = create_engine("sqlite:///mydb.db")

        with engine.connect() as conn:
            response = (
                Editor(conn, "users", "id")
                .fields(
                    Field("first_name").validator(Validate.required()),
                    Field("last_name").validator(Validate.required()),
                    Field("email"),
                )
                .process(request_body)
                .data()
                .to_dict()
            )
    """

    Action = Action
    version = "3.0.0"

    # ------------------------------------------------------------------
    # Constructor
    # ------------------------------------------------------------------

    def __init__(
        self,
        db: Connection = None,
        table: Union[str, List[str]] = None,
        pkey: Union[str, List[str]] = None,
    ) -> None:
        """Create an Editor instance.

        All parameters are optional — they can also be set later via the
        chainable setter methods.

        Args:
            db:    SQLAlchemy :class:`~sqlalchemy.engine.Connection`.
            table: Database table name or list of table names.
            pkey:  Primary key column name(s).  Defaults to ``'id'``.
        """
        super().__init__()

        self._db: Optional[Connection] = None
        self._fields: List[Field] = []
        self._process_data: Optional[DtRequest] = None
        self._id_prefix: str = "row_"
        self._join: List[Mjoin] = []
        self._pkey: List[str] = ["id"]
        self._table: List[str] = []
        self._read_table_names: List[str] = []
        self._transaction: bool = False
        self._where: List[Any] = []
        self._left_join: List[LeftJoin] = []
        self._out: DtResponse = DtResponse()
        self._events: Dict[str, List[Callable]] = {}
        self._validators: List[GlobalValidator] = []
        self._validators_after_fields: List[GlobalValidator] = []
        self._try_catch: bool = False
        self._upload_data: Optional[Dict] = None
        self._debug: bool = False
        self._debug_info: List[Any] = []
        self._left_join_remove: bool = False
        self._schema: Optional[str] = None
        self._write: bool = True
        self._do_validate: bool = True
        self._custom_get: Optional[GetFn] = None

        if db is not None:
            self.db(db)
        if table is not None:
            self.table(table)
        if pkey is not None:
            self.pkey(pkey)

    # ------------------------------------------------------------------
    # Public static method
    # ------------------------------------------------------------------

    @staticmethod
    def action(http: DtRequest) -> Action:
        """Determine the action type from a request object.

        When ``http`` is ``None`` or has no ``action`` field the method returns
        :attr:`Action.READ`.

        Args:
            http: The parsed DataTables / Editor request.

        Returns:
            The matching :class:`Action` enum value.

        Raises:
            ValueError: If the ``action`` string is not recognised.
        """
        if not http or not http.action:
            return Action.READ

        mapping = {
            "read": Action.READ,
            "create": Action.CREATE,
            "edit": Action.EDIT,
            "remove": Action.DELETE,
            "upload": Action.UPLOAD,
            "search": Action.SEARCH,
        }

        if http.action not in mapping:
            raise ValueError(f"Unknown Editor action: {http.action}")

        return mapping[http.action]

    # ------------------------------------------------------------------
    # Public chainable API
    # ------------------------------------------------------------------

    def data(self) -> DtResponse:
        """Return the :class:`~datatables_server.types.DtResponse` built by this instance.

        Call :meth:`process` first to populate the response.

        Returns:
            The response object.
        """
        return self._out

    def db(self, db: Connection = None) -> Union["Editor", Connection]:
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

    def db_transaction(self) -> Optional[Any]:
        """Return the current database transaction, if any.

        Returns:
            The active transaction context, or ``None``.
        """
        return None  # Synchronous API — transactions are managed externally

    def debug(self, param: Any = None) -> Union["Editor", bool]:
        """Get or set debug mode, or append a debug message.

        * Called with no arguments — returns the current ``bool`` debug flag.
        * Called with ``True`` or ``False`` — enables / disables debug mode
          and returns ``self``.
        * Called with any other value — appends that value to the debug info
          list and returns ``self``.

        Args:
            param: Debug flag or message.  Omit to use as a getter.

        Returns:
            Current debug flag (getter) or ``self`` (setter / chaining).
        """
        if param is None:
            return self._debug

        if param is True or param is False:
            self._debug = param
            return self

        # Any other value is treated as a debug message
        self._debug_info.append(param)
        return self

    def do_validate(self, do_validate: bool = None) -> Union["Editor", bool]:
        """Get or set whether field validation is performed.

        When set to ``False``, the :meth:`validate` method always returns
        ``True`` without running any validators.  Useful when you have
        pre-validated the data yourself.

        Args:
            do_validate: ``True`` (default) to enable validation; ``False`` to
                skip.  Omit to use as a getter.

        Returns:
            Current boolean value (getter) or ``self`` (setter / chaining).
        """
        if do_validate is None:
            return self._do_validate

        self._do_validate = do_validate
        return self

    def field(self, name_or_field: Union[str, Field]) -> Union["Editor", Field]:
        """Get a field by name, or add a field instance.

        Args:
            name_or_field: A :class:`~datatables_server.field.Field` instance
                to add, or a field name string to look up.

        Returns:
            The matching :class:`~datatables_server.field.Field` when a string
            is passed (raises ``ValueError`` if not found), or ``self`` for
            chaining when a :class:`~datatables_server.field.Field` is passed.

        Raises:
            ValueError: When a string is passed and no matching field exists.
        """
        if isinstance(name_or_field, str):
            for f in self._fields:
                if f.name() == name_or_field:
                    return f
            raise ValueError(f"Unknown field: {name_or_field}")

        self._fields.append(name_or_field)
        return self

    def fields(self, *fields: Field) -> Union["Editor", List[Field]]:
        """Get all registered fields, or add one or more fields.

        Called with no arguments acts as a getter.  Called with arguments,
        appends them and returns ``self`` for chaining.

        Args:
            *fields: Zero or more :class:`~datatables_server.field.Field`
                instances to add.

        Returns:
            Current field list (getter) or ``self`` (setter / chaining).
        """
        if not fields:
            return self._fields

        self._fields.extend(fields)
        return self

    def get(self, fn: GetFn) -> "Editor":
        """Set a custom GET function to replace the built-in data retrieval.

        When set, the callable is invoked by :meth:`_get` instead of
        executing the standard SELECT query.  The callable receives the same
        ``(id, http)`` arguments and must return a
        :class:`~datatables_server.types.DtResponse`.

        Args:
            fn: The custom GET callable.

        Returns:
            ``self`` for method chaining.
        """
        self._custom_get = fn
        return self

    def id_prefix(self, id_prefix: str = None) -> Union["Editor", str]:
        """Get or set the row ID prefix (default: ``'row_'``).

        DataTables requires a string DOM ID for each row.  Because primary keys
        are often numeric and not valid HTML IDs on their own, a prefix is
        prepended.

        Args:
            id_prefix: New prefix string.  Omit to use as a getter.

        Returns:
            Current prefix string (getter) or ``self`` (setter / chaining).
        """
        if id_prefix is None:
            return self._id_prefix

        self._id_prefix = id_prefix
        return self

    def in_data(self) -> Optional[DtRequest]:
        """Return the request data currently being processed.

        Only useful after :meth:`process` has been called.

        Returns:
            The :class:`~datatables_server.types.DtRequest` passed to
            :meth:`process`, or ``None`` if not yet called.
        """
        return self._process_data

    def join(self, *join: Mjoin) -> Union["Editor", List[Mjoin]]:
        """Get all registered :class:`~datatables_server.mjoin.Mjoin` instances, or add more.

        Args:
            *join: Zero or more :class:`~datatables_server.mjoin.Mjoin` instances
                to add.

        Returns:
            Current Mjoin list (getter) or ``self`` (setter / chaining).
        """
        if not join:
            return self._join

        self._join.extend(join)
        return self

    def left_join(
        self,
        table: str,
        field1_or_fn: Union[str, Callable] = None,
        operator: str = None,
        field2: str = None,
    ) -> "Editor":
        """Add a LEFT JOIN for reading across multiple tables.

        Two call signatures are supported:

        * ``left_join(table, fn)`` — *field1_or_fn* is a callable that receives
          the current SELECT statement and must return a modified statement.
        * ``left_join(table, field1, operator, field2)`` — plain field join.

        On create / edit, Editor will automatically write to all joined tables.
        On delete, joined table rows are optionally removed (see
        :meth:`left_join_remove`).

        Args:
            table:        Name of the table to join onto.
            field1_or_fn: Left-hand field (e.g. ``'users.dept_id'``) or a
                statement-mutating callable.
            operator:     Comparison operator (e.g. ``'='``).
            field2:       Right-hand field (e.g. ``'departments.id'``).

        Returns:
            ``self`` for method chaining.
        """
        if callable(field1_or_fn):
            self._left_join.append(LeftJoin(table=table, fn=field1_or_fn))
        else:
            self._left_join.append(
                LeftJoin(
                    table=table,
                    field1=field1_or_fn,
                    operator=operator,
                    field2=field2,
                )
            )
        return self

    def left_join_remove(self, remove: bool = None) -> Union["Editor", bool]:
        """Get or set whether to delete from left-joined tables on row deletion.

        Disabled by default.  Prefer ``ON DELETE CASCADE`` at the database
        level instead.

        Args:
            remove: ``True`` to enable, ``False`` to disable.  Omit to use as
                a getter.

        Returns:
            Current boolean value (getter) or ``self`` (setter / chaining).
        """
        if remove is None:
            return self._left_join_remove

        self._left_join_remove = remove
        return self

    def on(self, name: str, callback: Callable) -> "Editor":
        """Add an event listener.

        Multiple listeners may be registered for the same event; they are
        called in registration order.

        Available events:

        * ``'preGet'``, ``'postGet'``
        * ``'preCreate'``, ``'validatedCreate'``, ``'writeCreate'``,
          ``'postCreate'``, ``'postCreateAll'``
        * ``'preEdit'``, ``'validatedEdit'``, ``'writeEdit'``,
          ``'postEdit'``, ``'postEditAll'``
        * ``'preRemove'``, ``'postRemove'``, ``'postRemoveAll'``
        * ``'preUpload'``, ``'postUpload'``
        * ``'processed'``

        All callbacks receive the :class:`Editor` instance as their first
        argument, followed by event-specific arguments.

        Args:
            name:     Event name string.
            callback: The listener callable.

        Returns:
            ``self`` for method chaining.
        """
        if name not in self._events:
            self._events[name] = []
        self._events[name].append(callback)
        return self

    def schema(self, schema: str = None) -> Union["Editor", str]:
        """Get or set the database schema.

        Useful when working with a non-default schema.  When set, it is
        prepended to table names in raw SQL fragments.

        Args:
            schema: Schema name.  Omit to use as a getter.

        Returns:
            Current schema string (getter) or ``self`` (setter / chaining).
        """
        if schema is None:
            return self._schema

        self._schema = schema
        return self

    def read_table(self, table: Union[str, List[str]] = None) -> Union["Editor", List[str]]:
        """Get or set a separate table name for read operations.

        When set, Editor will use this table (or view) for SELECT queries while
        still writing to the tables set via :meth:`table`.  This is particularly
        useful when a database VIEW provides a complex SELECT.

        Args:
            table: Table / view name or list thereof.  Omit to use as a getter.

        Returns:
            Current read-table list (getter) or ``self`` (setter / chaining).
        """
        if table is None:
            return self._read_table_names

        if isinstance(table, str):
            self._read_table_names.append(table)
        else:
            self._read_table_names.extend(table)

        return self

    def table(self, table: Union[str, List[str]] = None) -> Union["Editor", List[str]]:
        """Get or set the database table name(s) used for write operations.

        Table names may include an alias (e.g. ``'users as u'``).

        Args:
            table: Table name or list of table names.  Omit to use as a getter.

        Returns:
            Current table list (getter) or ``self`` (setter / chaining).
        """
        if table is None:
            return self._table

        if isinstance(table, str):
            self._table.append(table)
        else:
            self._table.extend(table)

        return self

    def transaction(self, transaction: bool = None) -> Union["Editor", bool]:
        """Get or set whether to wrap processing in a database transaction.

        When enabled, :meth:`process` wraps the entire :meth:`_process` call in
        a ``begin_nested()`` savepoint.

        Args:
            transaction: ``True`` to enable; ``False`` to disable.  Omit to
                use as a getter.

        Returns:
            Current boolean value (getter) or ``self`` (setter / chaining).
        """
        if transaction is None:
            return self._transaction

        self._transaction = transaction
        return self

    def pkey(self, pkey: Union[str, List[str]] = None) -> Union["Editor", List[str]]:
        """Get or set the primary key column name(s).

        For compound primary keys pass a list of column names.  The default
        value is ``['id']``.

        Args:
            pkey: Column name or list of column names.  Omit to use as a getter.

        Returns:
            Current primary key list (getter) or ``self`` (setter / chaining).
        """
        if pkey is None:
            return self._pkey

        if isinstance(pkey, str):
            self._pkey = [pkey]
        else:
            self._pkey = list(pkey)

        return self

    def pkey_to_value(self, row: Dict[str, Any], flat: bool = False) -> str:
        """Convert a row's primary key columns to a single combined string.

        For single-column primary keys the value is simply ``str(val)``.  For
        compound keys the values are joined with the separator produced by
        :meth:`_pkey_separator`.

        Args:
            row:  The row data dict.
            flat: If ``True``, read directly from the flat dict using the
                column name as a key.  If ``False``, use dot-notation nesting
                via :meth:`~datatables_server.nested_data.NestedData._read_prop`.

        Returns:
            Combined primary key string.

        Raises:
            ValueError: If a required primary key component is missing from
                *row*.
        """
        pkey = self.pkey()
        parts = []

        for column in pkey:
            if flat:
                val = row.get(column) if isinstance(row, dict) else None
            else:
                val = self._read_prop(column, row)

            if val is None:
                raise ValueError("Primary key element is not available in the data set")

            parts.append(str(val))

        return self._pkey_separator().join(parts)

    def pkey_to_object(
        self,
        value: str,
        flat: bool = False,
        pkey: List[str] = None,
    ) -> Dict[str, Any]:
        """Convert a combined primary key string back to a dict of field values.

        This is the inverse of :meth:`pkey_to_value`.

        Args:
            value: The combined primary key string.  May include the
                :meth:`id_prefix` — it is stripped automatically.
            flat:  If ``True``, return a flat ``{column: value}`` dict.  If
                ``False``, use nested dot-notation via
                :meth:`~datatables_server.nested_data.NestedData._write_prop`.
            pkey:  Override the primary key column list.  Defaults to
                :meth:`pkey`.

        Returns:
            Dict of primary key column name → value.

        Raises:
            ValueError: If the number of parts in *value* does not match the
                number of primary key columns.
        """
        arr: Dict[str, Any] = {}
        value = value.replace(self.id_prefix(), "")
        id_parts = value.split(self._pkey_separator())

        if pkey is None:
            pkey = self.pkey()

        if len(pkey) != len(id_parts):
            raise ValueError("Primary key data does not match submitted data")

        for i, col in enumerate(pkey):
            if flat:
                arr[col] = id_parts[i]
            else:
                self._write_prop(arr, col, id_parts[i])

        return arr

    def process(self, data: Union[DtRequest, Dict], files: Dict = None) -> "Editor":
        """Process a DataTables / Editor request.

        This is the main entry point.  Pass the parsed request body (as either a
        :class:`~datatables_server.types.DtRequest` or a plain ``dict`` from a
        JSON body) and this method performs the appropriate database operations.

        When :meth:`transaction` is enabled the processing is wrapped in a
        ``begin_nested()`` savepoint so that any failure causes a full rollback.
        When :meth:`try_catch` is enabled, exceptions are caught and stored in
        ``self.data().error`` rather than re-raised.

        Args:
            data:  Request data — either a
                :class:`~datatables_server.types.DtRequest` or a raw ``dict``.
            files: Optional file-upload information dict.

        Returns:
            ``self`` for method chaining.
        """
        if not isinstance(data, DtRequest):
            data = self._dict_to_dt_request(parse_form_data(data))

        if self._debug:
            self._debug_info.append(f"DataTables server (Python) version {self.version}")

        # Determine whether this request will write to the database so we know
        # whether to commit afterwards.  Read-only actions (READ, SEARCH) must
        # not trigger a commit.  Use the resolved Action enum rather than the
        # raw action string, because the client sends 'remove' but the enum
        # value is 'delete' — comparing raw strings would miss that case.
        _write_actions = {Action.CREATE, Action.EDIT, Action.DELETE, Action.UPLOAD}
        _is_write = Editor.action(data) in _write_actions

        if self._transaction:
            try:
                with self._db.begin_nested():
                    if self._try_catch:
                        try:
                            self._process(data, files)
                        except Exception as e:
                            self._out.error = str(e)
                    else:
                        self._process(data, files)
            except Exception as e:
                if self._try_catch:
                    self._out.error = str(e)
                else:
                    raise
        else:
            if self._try_catch:
                try:
                    self._process(data, files)
                except Exception as e:
                    self._out.error = str(e)
            else:
                self._process(data, files)

        # Commit the connection's transaction after any successful write.
        # SQLAlchemy connections do not auto-commit; without this the INSERTs,
        # UPDATEs and DELETEs executed above are rolled back when the connection
        # is returned to the pool.  Read actions are left uncommitted (they
        # carry no pending writes, so this is a no-op for them).
        if _is_write and not self._out.error:
            self._db.commit()

        return self

    def try_catch(self, try_catch: bool = None) -> Union["Editor", bool]:
        """Get or set whether to catch exceptions during :meth:`process`.

        When ``True``, any unhandled exception in :meth:`_process` is caught
        and its message stored in :attr:`~datatables_server.types.DtResponse.error`
        rather than propagating to the caller.

        Args:
            try_catch: ``True`` to enable; ``False`` to disable.  Omit to use
                as a getter.

        Returns:
            Current boolean value (getter) or ``self`` (setter / chaining).
        """
        if try_catch is None:
            return self._try_catch

        self._try_catch = try_catch
        return self

    def validate(self, errors: List[DtError], http: DtRequest) -> bool:
        """Run field-level (and global after-field) validation on submitted data.

        Skips validation when:

        * :meth:`do_validate` is ``False``.
        * The action is neither ``'create'`` nor ``'edit'``.
        * No ``data`` dict was submitted.

        Args:
            errors: List to append :class:`~datatables_server.types.DtError`
                instances to.
            http:   The parsed request to validate.

        Returns:
            ``True`` if all validation passed, ``False`` if any errors were
            added to *errors* or a global after-field validator returned a
            string.
        """
        if not self._do_validate:
            return True

        if http.action not in ("create", "edit"):
            return True

        if not http.data:
            return True

        id_prefix = self.id_prefix()
        fields = self.fields()

        for key, values in http.data.items():
            for field in fields:
                id_ = key.replace(id_prefix, "")
                result = field.validate(values, self, id_, http.action)
                if result is not True:
                    errors.append(DtError(id=id_, name=field.name(), status=str(result)))

            # Mjoin validation
            for mj in self._join:
                mj.validate(errors, self, values, http.action)

        # Global after-field validators
        for validator in self._validators_after_fields:
            ret = validator(self, http.action or "", http)
            if isinstance(ret, str):
                self._out.error = ret
                return False

        return len(errors) == 0

    def validator(
        self,
        after_fields: Union[bool, GlobalValidator] = None,
        fn: GlobalValidator = None,
    ) -> Union["Editor", List[GlobalValidator]]:
        """Get or set global validators.

        Global validators run for all write actions (create, edit, remove).
        They receive the :class:`Editor` instance, action string, and request
        data.  Return ``True`` to pass, or a string error message to fail.

        Four call signatures:

        * ``validator()`` → returns the list of *pre*-field validators.
        * ``validator(True)`` → returns the list of *post*-field validators.
        * ``validator(fn)`` → registers *fn* as a pre-field validator.
        * ``validator(True, fn)`` → registers *fn* as a post-field validator.
        * ``validator(False, fn)`` → registers *fn* as a pre-field validator.

        Args:
            after_fields: Controls getter scope or setter timing.
            fn:           The validator callable (for setter forms).

        Returns:
            Validator list (getter) or ``self`` (setter / chaining).
        """
        # Normalise arguments (mirrors TypeScript argument-shifting logic)
        if after_fields is None:
            after_fields = False
        elif callable(after_fields):
            fn = after_fields
            after_fields = False

        # Getter path
        if fn is None:
            return self._validators_after_fields if after_fields else self._validators

        # Setter path
        if after_fields:
            self._validators_after_fields.append(fn)
        else:
            self._validators.append(fn)

        return self

    def where(self, *cond: Any) -> Union["Editor", List[Any]]:
        """Get the current WHERE conditions, or append one or more.

        WHERE conditions are applied to every SELECT, UPDATE and DELETE query
        executed by this instance.

        Args:
            *cond: Zero or more SQLAlchemy WHERE expressions.

        Returns:
            Current condition list (getter) or ``self`` (setter / chaining).
        """
        if not cond:
            return self._where

        self._where.extend(cond)
        return self

    def where_clear(self) -> "Editor":
        """Remove all WHERE conditions from this instance.

        Returns:
            ``self`` for method chaining.
        """
        self._where = []
        return self

    def write(self, write_val: bool = None) -> Union["Editor", bool]:
        """Get or set whether write operations are allowed.

        When ``False``, create, edit, delete and upload requests are silently
        ignored.

        Args:
            write_val: ``True`` (default) to allow writes; ``False`` to make
                the instance read-only.  Omit to use as a getter.

        Returns:
            Current boolean value (getter) or ``self`` (setter / chaining).
        """
        if write_val is None:
            return self._write

        self._write = write_val
        return self

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _pk_where_conds(self, pk_obj: Dict[str, Any]) -> List:
        """Convert a primary key dict into SQLAlchemy WHERE clause elements.

        ``pkey_to_object`` returns keys like ``'users.id'`` when left joins are
        active (because ``_prep_join`` qualifies the pkey with the table name).
        Passing such a key to ``sa.column()`` causes the database driver to
        quote the entire string as a single identifier (e.g. backtick-quoted
        ``users.id`` on MySQL), which is not a valid column reference.  Using
        ``sa.literal_column()`` instead emits the text verbatim so the
        database parser sees it as a qualified ``table.column`` reference.

        Args:
            pk_obj: Dict of ``{pkey_column: value}`` as returned by
                :meth:`pkey_to_object` with ``flat=True``.

        Returns:
            List of SQLAlchemy binary-expression clause elements.
        """
        return [sa.literal_column(k) == v for k, v in pk_obj.items()]

    def _exec(self, stmt: Any) -> Any:
        """Execute *stmt* against the database, logging SQL when debug is active.

        All database writes (INSERT, UPDATE, DELETE) and reads (SELECT) in the
        library go through this method so that a single debug flag causes every
        statement to be captured and returned in the response.

        Args:
            stmt: Any SQLAlchemy executable statement.

        Returns:
            The ``CursorResult`` returned by ``Connection.execute``.
        """
        if self._debug:
            try:
                compiled = stmt.compile(
                    dialect=self._db.dialect,
                    compile_kwargs={"literal_binds": True},
                )
                self._debug_info.append(str(compiled))
            except Exception:
                try:
                    # Fallback: compile without literal binds (shows bind params)
                    self._debug_info.append(str(stmt.compile(dialect=self._db.dialect)))
                except Exception:
                    self._debug_info.append(repr(stmt))
        return self._db.execute(stmt)

    def _alias(self, name: str, type: str = "alias") -> str:
        """Extract either the alias or the original part of a ``'table as alias'`` string.

        Args:
            name: Table name, possibly with an ``AS`` alias
                (e.g. ``'users as u'`` or ``'users u'``).
            type: ``'alias'`` to return the alias part (default); ``'orig'``
                to return the original table name.

        Returns:
            The alias portion, the original portion, or *name* unchanged if no
            alias is present.
        """
        if " as " in name.lower():
            parts = re.split(r" as ", name, flags=re.IGNORECASE)
            return parts[1].strip() if type == "alias" else parts[0].strip()

        if " " in name:
            parts = name.split(" ", 1)
            return parts[1].strip() if type == "alias" else parts[0].strip()

        return name

    def _file_clean(self) -> None:
        """Trigger ``dbCleanExec`` on every field with an upload configuration.

        Removes orphaned uploaded files from the database.
        """
        for field in self._fields:
            upload = field.upload()
            if upload:
                upload.db_clean_exec(self, field)

        for mj in self._join:
            for field in mj.fields():
                upload = field.upload()
                if upload:
                    upload.db_clean_exec(self, field)

    def _file_data(
        self,
        limit_table: str = None,
        ids: List[str] = None,
        data: List[Any] = None,
    ) -> Dict:
        """Collect file information for every field with an upload configuration.

        Args:
            limit_table: When provided, only fetch file data for this specific
                upload table.
            ids:         Specific file IDs to retrieve (for performance).
            data:        Already-fetched row data (used to derive file IDs).

        Returns:
            A ``{table_name: {id: file_info}}`` dict.
        """
        files: Dict = {}

        self._file_data_fields(files, self._fields, limit_table, ids, data)

        for mj in self._join:
            join_data: Optional[List[Any]] = None
            if data is not None:
                join_data = []
                for row in data:
                    inner = row.get(mj.name())
                    if inner:
                        join_data.extend(inner)

            self._file_data_fields(files, mj.fields(), limit_table, ids, join_data)

        return files

    def _file_data_fields(
        self,
        files: Dict,
        fields: List[Field],
        limit_table: str = None,
        ids: List[str] = None,
        data: List[Any] = None,
    ) -> None:
        """Collect file information for a specific list of fields.

        Args:
            files:       The files dict to populate (mutated in-place).
            fields:      Fields to inspect for upload configurations.
            limit_table: Optional table filter.
            ids:         Seed file IDs list (extended from *data*).
            data:        Row data used to extract file ID values.
        """
        for field in fields:
            upload = field.upload()
            if not upload:
                continue

            table = upload.table()
            if not table:
                continue

            if limit_table is not None and table != limit_table:
                continue

            if table in files:
                continue

            if ids is None:
                ids = []

            if data is not None:
                for row in data:
                    val = field.val("set", row)
                    if val:
                        ids.append(val)

                if not ids:
                    return

                if len(ids) > 1000:
                    ids = None

            file_data = upload.data(self.db(), ids)
            if file_data:
                files[table] = file_data

    def _find_field(self, name: str, type: str) -> Optional[Field]:
        """Find a field by its name or database column.

        Args:
            name: The name to search for.
            type: ``'name'`` to match against :meth:`Field.name`; ``'db'``
                to match against :meth:`Field.db_field`.

        Returns:
            The matching :class:`~datatables_server.field.Field`, or ``None``.
        """
        for field in self._fields:
            if type == "name" and field.name() == name:
                return field
            if type == "db" and field.db_field() == name:
                return field
        return None

    def _get(
        self,
        id: Union[str, List[str], None],
        http: DtRequest = None,
    ) -> DtResponse:
        """Read row data from the database.

        This is the core read path.  It:

        1. Fires the ``'preGet'`` event (cancels if the handler returns ``False``).
        2. Delegates to :attr:`_custom_get` when one is configured.
        3. Otherwise builds a SELECT over all readable fields, applies WHERE
           conditions, LEFT JOINs, optional ID filtering, SearchBuilder, and
           server-side processing (SSP).
        4. Executes the query and builds the output row list (with ``DT_RowId``).
        5. Runs each :class:`~datatables_server.mjoin.Mjoin`'s
           :meth:`~datatables_server.mjoin.Mjoin.data` method.
        6. Collects file data.
        7. Fires the ``'postGet'`` event.

        Args:
            id:   Specific row ID(s) to retrieve, or ``None`` for all rows.
            http: The parsed request (used for SSP, SearchBuilder, etc.).

        Returns:
            A populated :class:`~datatables_server.types.DtResponse`.
        """
        cancel = self._trigger("preGet", id)
        if cancel is False:
            return DtResponse()

        if self._custom_get:
            response = self._custom_get(id, http)
        else:
            fields = self.fields()
            pkeys = self.pkey()

            # ---- Build SELECT columns ----------------------------------------
            select_cols = []

            for pk in pkeys:
                select_cols.append(sa.literal_column(pk).label(pk))

            for f in fields:
                if f.db_field() in pkeys:
                    continue
                if f.apply("get") and not f._get_value_set:
                    db_field = f.db_field()
                    # Use literal_column to emit the expression verbatim (preserves
                    # dot-notation for table-qualified names)
                    select_cols.append(sa.literal_column(db_field).label(db_field))

            read_tbl = self._read_table()[0]
            stmt = sa.select(*select_cols).select_from(sa.text(read_tbl))

            # ---- WHERE conditions from self._where ---------------------------
            stmt = self._get_where(stmt)

            # ---- LEFT JOINs -------------------------------------------------
            stmt = _apply_left_joins(stmt, self._left_join)

            # ---- Filter by specific IDs -------------------------------------
            if id is not None:
                if isinstance(id, list):
                    or_conds = [
                        sa.and_(*self._pk_where_conds(self.pkey_to_object(single_id, flat=True))) for single_id in id
                    ]
                    stmt = stmt.where(sa.or_(*or_conds))
                else:
                    for cond in self._pk_where_conds(self.pkey_to_object(id, flat=True)):
                        stmt = stmt.where(cond)

            # ---- SearchBuilder ----------------------------------------------
            if http and http.search_builder and http.search_builder.get("criteria"):
                from .search_pane_options import construct_search_builder_query

                stmt = construct_search_builder_query(stmt, http.search_builder)

            # ---- Specific IDs refresh (from client-side) --------------------
            if http and http.ids:
                id_conds = [
                    sa.and_(*self._pk_where_conds(self.pkey_to_object(rid.replace(self.id_prefix(), ""), flat=True)))
                    for rid in http.ids
                ]
                stmt = stmt.where(sa.or_(*id_conds))

            # ---- Server-side processing (SSP) --------------------------------
            stmt, ssp = self._ssp(stmt, http)

            # ---- Execute ----------------------------------------------------
            result = self._exec(stmt)
            db_rows = [dict(r._mapping) for r in result]

            # ---- Build output rows ------------------------------------------
            out = []
            for row in db_rows:
                inner: Dict[str, Any] = {"DT_RowId": self.id_prefix() + self.pkey_to_value(row, flat=True)}
                for f in fields:
                    if f.apply("get") and f.http():
                        f.write(inner, row)
                out.append(inner)

            response = DtResponse(
                data=out,
                draw=ssp.draw,
                files={},
                options={},
                records_filtered=ssp.records_filtered,
                records_total=ssp.records_total,
            )

            # ---- Mjoin data -------------------------------------------------
            for mj in self._join:
                mj.data(self, response)

        response.files = self._file_data(None, None, response.data)
        self._trigger("postGet", id, response.data)
        return response

    def _get_where(self, stmt: sa.Select) -> sa.Select:
        """Apply all stored WHERE conditions to *stmt*.

        Conditions can be either:
        - SQLAlchemy clause elements (e.g. ``sa.column('x') == 1``)
        - Callables that accept the statement and return a modified statement
          (e.g. ``lambda s: s.where(sa.column('x') == 1)``)

        Args:
            stmt: The SELECT statement to extend.

        Returns:
            The (possibly modified) SELECT statement.
        """
        for cond in self._where:
            if callable(cond):
                stmt = cond(stmt)
            else:
                stmt = stmt.where(cond)
        return stmt

    def _insert(self, values: Dict) -> Optional[str]:
        """Insert a new parent row and trigger related join inserts.

        1. Validates that compound-PK values are present.
        2. Fires ``'validatedCreate'``.
        3. Calls :meth:`_insert_or_update` with ``id=None``.
        4. Merges any submitted PK values.
        5. Calls :meth:`~datatables_server.mjoin.Mjoin.create` on all joins.
        6. Fires ``'writeCreate'``.

        Args:
            values: The submitted row data dict.

        Returns:
            The new row's primary key string, or ``None`` on failure.
        """
        # Collect all set-values so compound-PK validation works
        all_vals: Dict[str, Any] = {}
        for f in self._fields:
            v = f.val("set", values)
            if v is not None:
                self._write_prop(all_vals, f.name(), v)

        self._pkey_validate_insert(all_vals)
        self._trigger("validatedCreate", values)

        id_ = self._insert_or_update(None, values)
        if id_ is None:
            return None

        # If the PK is compound, compute it from the submitted data; otherwise
        # merge any submitted single-PK value
        if len(self._pkey) > 1:
            id_ = self.pkey_to_value(all_vals)
        else:
            id_ = self._pkey_submit_merge(id_, all_vals)

        for mj in self._join:
            mj.create(self, id_, values)

        self._trigger("writeCreate", id_, values)
        return id_

    def _insert_or_update(self, id: Optional[str], values: Dict) -> Optional[str]:
        """Coordinate INSERT or UPDATE across all tables (main + left-joined).

        For each table in :meth:`table` the appropriate operation is performed.
        Then, for each LEFT JOIN (that uses a simple field condition), the
        linked rows are inserted or updated.

        Args:
            id:     Row primary key for update; ``None`` for insert.
            values: The submitted row data dict.

        Returns:
            The primary key string (from the first INSERT that generates one),
            or ``None``.
        """
        tables = self.table()
        result_id: Optional[str] = None

        for tbl in tables:
            where = self.pkey_to_object(id, True) if id is not None else None
            res = self._insert_or_update_table(tbl, values, where)
            if res is not None and result_id is None:
                result_id = res

        # Handle left-joined tables
        for lj in self._left_join:
            if lj.fn:
                # Cannot determine join condition from a callable — skip
                continue

            join_table_alias = self._alias(lj.table, "alias")
            table_part = self._part(lj.field1)
            if self._part(lj.field1, "db"):
                table_part = self._part(lj.field1, "db") + "." + table_part

            if table_part == join_table_alias:
                parent_link = lj.field2
                child_link = lj.field1
            else:
                parent_link = lj.field1
                child_link = lj.field2

            # Determine the WHERE value for the joined row.
            # For creates, result_id holds the freshly generated PK.
            # For edits, result_id is None (UPDATE returns nothing) so fall
            # back to the original id that was passed in.
            where_val = None
            if parent_link == self._pkey[0] and len(self._pkey) == 1:
                where_val = result_id if result_id is not None else id
            else:
                field = self._find_field(parent_link, "db")
                if not field or not field.apply("edit", values):
                    field = self._find_field(child_link, "db")
                    if not field or not field.apply("edit", values):
                        continue

                where_val = field.val("set", values)

            where_col_name = self._part(child_link, "column")
            self._insert_or_update_table(lj.table, values, {where_col_name: where_val})

        return result_id

    def _insert_or_update_table(
        self,
        table: str,
        values: Dict,
        where: Dict = None,
    ) -> Optional[str]:
        """Perform the actual INSERT or UPDATE on a single table.

        Iterates over all fields to build the set-values dict, applying table
        and action filters.  Then executes the appropriate statement.

        For the main table on INSERT, attempts to use ``RETURNING`` (PostgreSQL)
        and falls back to ``lastrowid`` (SQLite / MySQL).

        Args:
            table:  Table name (may include alias).
            values: Submitted row data.
            where:  If provided, this is an UPDATE with these conditions;
                if ``None``, this is an INSERT.

        Returns:
            The generated primary key string (INSERT on main table only), or
            ``None``.
        """
        action = "create" if where is None else "edit"
        table_alias = self._alias(table, "alias")
        set_vals: Dict[str, Any] = {}

        for field in self.fields():
            table_part = self._part(field.db_field())
            if self._part(field.db_field(), "db"):
                table_part = self._part(field.db_field(), "db") + "." + table_part

            # Skip fields that belong to a different table (only enforced when
            # joins are present)
            if self._left_join and table_part != table_alias:
                continue

            if not field.apply(action, values):
                continue

            field_col = self._part(field.db_field(), "column")
            set_vals[field_col] = field.val("set", values)

        if not set_vals:
            return None

        if action == "create" and table in self.table():
            # Insert on the main table — return the generated PK.
            # Use RETURNING when the dialect supports it (PostgreSQL, SQLite >= 3.35);
            # fall back to lastrowid for dialects that don't (MySQL/MariaDB).
            pkey_col = self._part(self._pkey[0], "column")
            tbl_obj = sa.table(table, *[sa.column(c) for c in set_vals.keys()])
            ins_stmt = sa.insert(tbl_obj).values(set_vals)

            if getattr(self._db.dialect, "insert_returning", False):
                ins_stmt = ins_stmt.returning(sa.column(pkey_col))
                result = self._exec(ins_stmt)
                row = result.fetchone()
                return str(row[0]) if row else None
            else:
                result = self._exec(ins_stmt)
                return str(result.lastrowid) if result.lastrowid else None

        elif action == "create":
            # Insert on a secondary / left-joined table
            tbl_obj = sa.table(table, *[sa.column(c) for c in set_vals.keys()])
            self._exec(sa.insert(tbl_obj).values(set_vals))

        elif table not in self.table() and where:
            # Non-main table on edit — check existence before decide insert/update.
            # Strip any table prefix from WHERE keys (e.g. 'users.id' -> 'id') so
            # that sa.column() refers to a bare column name, not a dotted identifier.
            bare_where = {self._part(k, "column") if "." in k else k: v for k, v in where.items()}
            where_conds = [sa.column(k) == v for k, v in bare_where.items()]
            check = list(self._exec(sa.select(sa.text("*")).select_from(sa.text(table)).where(sa.and_(*where_conds))))
            if check:
                all_cols = list(set_vals.keys()) + [k for k in bare_where if k not in set_vals]
                upd_tbl = sa.table(table, *[sa.column(c) for c in all_cols])
                self._exec(sa.update(upd_tbl).where(sa.and_(*where_conds)).values(**set_vals))
            else:
                merged = {**set_vals, **bare_where}
                tbl_obj = sa.table(table, *[sa.column(c) for c in merged.keys()])
                self._exec(sa.insert(tbl_obj).values(merged))

        elif where:
            # Main table update — same bare-column treatment for the WHERE.
            bare_where = {self._part(k, "column") if "." in k else k: v for k, v in where.items()}
            where_conds = [sa.column(k) == v for k, v in bare_where.items()]
            all_cols = list(set_vals.keys()) + [k for k in bare_where if k not in set_vals]
            upd_tbl = sa.table(table, *[sa.column(c) for c in all_cols])
            self._exec(sa.update(upd_tbl).where(sa.and_(*where_conds)).values(**set_vals))

        return None

    def _options(self, refresh: bool) -> None:
        """Load option lists for all fields and Mjoin instances.

        Populates ``self._out.options``, ``self._out.search_panes``,
        ``self._out.search_builder``, and ``self._out.column_control`` as
        appropriate.

        Args:
            refresh: ``True`` when called after a write operation; ``False``
                on the initial data load.
        """
        fields = self.fields()

        for field in fields:
            # Standard options (select, radio, etc.)
            opts_inst = field.options()
            if opts_inst:
                opts = opts_inst.exec(self._db, refresh)
                if opts is not False and opts is not None:
                    if self._out.options is None:
                        self._out.options = {}
                    self._out.options[field.name()] = opts

            # SearchPanes options
            sp_opts = field.search_pane_options_exec(field, self, self._process_data, fields, self._left_join, self._db)
            if sp_opts:
                if self._out.search_panes is None:
                    self._out.search_panes = {"options": {}}
                self._out.search_panes["options"][field.name()] = sp_opts

            # SearchBuilder options
            sb_opts = field.search_builder_options_exec(
                field, self, self._process_data, fields, self._left_join, self._db
            )
            if sb_opts:
                if self._out.search_builder is None:
                    self._out.search_builder = {"options": {}}
                self._out.search_builder["options"][field.name()] = sb_opts

            # ColumnControl
            cc = field.column_control()
            if cc:
                opts = cc.exec(self._db, False)
                if opts is not False and opts is not None:
                    if self._out.column_control is None:
                        self._out.column_control = {}
                    self._out.column_control[field.name()] = opts

        # Options from Mjoin instances
        for mj in self._join:
            if self._out.options is None:
                self._out.options = {}
            mj.options(self._out.options, self._db, refresh)

    def _options_search(self, http: DtRequest) -> None:
        """Handle a field-option search request (for autocomplete / lazy-load).

        Looks up the field by :attr:`~datatables_server.types.DtRequest.field`,
        then delegates to the field's :class:`~datatables_server.options.Options`
        instance to execute either a ``search`` or a ``find`` query.

        Args:
            http: The parsed request containing ``field``, ``search``, or
                ``values``.
        """
        if not http.field:
            return

        field = self._find_field(http.field, "name")
        if not field:
            return

        opts_inst = field.options()
        if not opts_inst:
            return

        values = None
        if http.search:
            search_term = http.search if isinstance(http.search, str) else http.search.get("value", "")
            values = opts_inst.search(self.db(), search_term)
        elif http.values:
            values = opts_inst.find(self.db(), http.values)

        if values is not None:
            self._out.data = values

    def _part(self, name: str, type: str = "table") -> str:
        """Extract the ``db``, ``table``, or ``column`` component from a qualified field name.

        Handles up to three levels of dot-notation: ``[db.]table.column``.

        Args:
            name: The field name, e.g. ``'users.first_name'`` or
                ``'mydb.users.first_name'``.
            type: The component to return: ``'db'``, ``'table'``
                (default), or ``'column'``.

        Returns:
            The requested component, or an empty string if not present.
        """
        db_part = ""
        table_part = ""
        column_part = ""

        if "." in name:
            parts = name.split(".")
            if len(parts) == 3:
                db_part, table_part, column_part = parts
            elif len(parts) == 2:
                table_part, column_part = parts
        else:
            column_part = name

        if type == "db":
            return db_part
        if type == "table":
            return table_part
        return column_part

    def _prep_join(self) -> None:
        """Validate that join configuration is consistent.

        When LEFT JOINs are in use:

        * Ensures every PK column has a table prefix.
        * Ensures every field has a table prefix (required so Editor can
          determine which table each field belongs to).

        Raises:
            ValueError: If a field is missing its table prefix when joins are
                configured.
        """
        if not self._left_join:
            return

        # Ensure PKs are table-qualified
        table_alias = self._alias(self.table()[0], "alias")
        for i, pk in enumerate(self._pkey):
            if "." not in pk:
                self._pkey[i] = f"{table_alias}.{pk}"

        # Ensure all fields are table-qualified
        for field in self._fields:
            name = field.db_field()
            if "." not in name:
                raise ValueError(
                    f'Table part of the field "{name}" was not found. '
                    "In Editor instances that use a join, all fields must have the "
                    "database table set explicitly."
                )

    def _pkey_separator(self) -> str:
        """Compute the separator string used between compound PK components.

        The separator is the CRC32 (hex) of the comma-joined primary key column
        names, mirroring the TypeScript implementation.

        Returns:
            Hex-encoded CRC32 string.
        """
        key_str = ",".join(self.pkey())
        crc = binascii.crc32(key_str.encode()) & 0xFFFFFFFF
        return format(crc, "x")

    def _pkey_submit_merge(self, pkey_val: str, row: Dict) -> str:
        """Merge a stored primary key value with values from the submitted row.

        If a field whose :meth:`~datatables_server.field.Field.db_field` matches
        a PK column was submitted (and passes ``apply('edit', …)``), the
        submitted value overwrites the stored value.  This handles the case
        where the PK itself is editable.

        Args:
            pkey_val: The existing primary key string.
            row:      The submitted row data.

        Returns:
            The (possibly updated) primary key string.
        """
        pkey = self._pkey
        arr = self.pkey_to_object(pkey_val, True)

        for column in pkey:
            field = self._find_field(column, "db")
            if field and field.apply("edit", row):
                arr[column] = field.val("set", row)

        return self.pkey_to_value(arr, True)

    def _pkey_validate_insert(self, row: Dict) -> bool:
        """Validate that compound PKs have all required values for an INSERT.

        Single-column PKs are auto-generated by the database so no validation
        is needed.

        Args:
            row: The submitted data (with set-values already resolved).

        Returns:
            Always ``True`` for single-column PKs.

        Raises:
            ValueError: When a compound PK column has no submitted value.
        """
        pkey = self.pkey()
        if len(pkey) == 1:
            return True

        for column in pkey:
            field = self._find_field(column, "db")
            if not field or not field.apply("create", row):
                raise ValueError(
                    "When inserting into a compound key table, all fields that are part "
                    "of the compound key must be submitted with a specific value."
                )

        return True

    def _process(self, data: DtRequest, upload: Dict) -> None:
        """Internal main processing dispatcher.

        Drives the full request lifecycle:

        1. Reset ``_out`` and store ``_process_data`` / ``_upload_data``.
        2. Run :meth:`_prep_join`.
        3. Run global pre-field validators.
        4. Dispatch to the appropriate action handler.
        5. Trigger the ``'processed'`` event.
        6. Attach debug info when enabled.

        Args:
            data:   The parsed request.
            upload: File-upload data (may be ``None``).
        """
        self._out = DtResponse(cancelled=[], data=[], field_errors=[], options={})
        self._process_data = data
        self._upload_data = upload
        self._prep_join()

        # Global pre-field validators
        for validator in self._validators:
            ret = validator(self, data.action or "", data)
            if isinstance(ret, str):
                self._out.error = ret
                break

        # Sanity-check: write actions must have data (or search/values for search)
        if (
            data.action
            and data.action not in ("upload", "read")
            and not data.data
            and not data.search
            and not data.values
        ):
            self._out.error = "No data detected."

        action = Editor.action(data)

        if not self._out.error:
            if action == Action.READ:
                out_data = self._get(None, data)
                # Merge all non-None fields from out_data into self._out
                for attr, val in vars(out_data).items():
                    if val is not None:
                        setattr(self._out, attr, val)
                self._options(False)

            elif action == Action.SEARCH:
                self._options_search(data)

            elif action == Action.UPLOAD and self._write:
                self._upload(data)

            elif action == Action.DELETE and self._write:
                self._remove(data)
                self._options(True)
                self._file_clean()

            elif action in (Action.CREATE, Action.EDIT) and self._write and data.data:
                keys = list(data.data.keys())

                # Pre-events — run before validation; cancelled rows are removed
                for id_src in list(keys):
                    values = data.data[id_src]
                    if action == Action.CREATE:
                        cancel = self._trigger("preCreate", values)
                    else:
                        id_ = id_src.replace(self.id_prefix(), "")
                        cancel = self._trigger("preEdit", id_, values)

                    if cancel is False:
                        del data.data[id_src]
                        self._out.cancelled.append(id_src)

                # Field validation
                valid = self.validate(self._out.field_errors, data)
                event_name = "Create" if action == Action.CREATE else "Edit"
                pkeys_info: List[Dict[str, Any]] = []

                if valid:
                    keys = list(data.data.keys())

                    for key in keys:
                        if action == Action.CREATE:
                            new_pkey = self._insert(data.data[key])
                        else:
                            new_pkey = self._update(key, data.data[key])

                        pkeys_info.append(
                            {
                                "data_key": self.id_prefix() + str(new_pkey),
                                "pkey": new_pkey,
                                "submit_key": key,
                            }
                        )

                    # Build submitted-data map keyed by the new row PK
                    submitted_data: Dict[str, Any] = {}
                    for key in data.data:
                        match = next((p for p in pkeys_info if p["submit_key"] == key), None)
                        if match and match["pkey"]:
                            submitted_data[match["pkey"]] = data.data[key]

                    self._trigger(
                        f"write{event_name}All",
                        [p["pkey"] for p in pkeys_info],
                        submitted_data,
                    )

                    # Re-fetch all affected rows in a single query
                    return_data = self._get([p["pkey"] for p in pkeys_info if p["pkey"]])
                    self._out.data = return_data.data

                    # Per-row post-events
                    for pk_info in pkeys_info:
                        matching_row = None
                        if return_data.data:
                            matching_row = next(
                                (r for r in return_data.data if r.get("DT_RowId") == pk_info["data_key"]),
                                None,
                            )
                        self._trigger(
                            f"post{event_name}",
                            pk_info["pkey"],
                            data.data.get(pk_info["submit_key"]),
                            matching_row,
                        )

                    self._trigger(
                        f"post{event_name}All",
                        [p["pkey"] for p in pkeys_info],
                        submitted_data,
                        return_data.data,
                    )

                    self._file_clean()

                self._options(True)

        self._trigger("processed", action, data, self._out)

        if self._debug:
            self._out.debug = list(self._debug_info)

    def _read_table(self) -> List[str]:
        """Return the read table name(s), falling back to the write table(s).

        Returns:
            List of table names used for SELECT queries.
        """
        return self._read_table_names if self._read_table_names else self._table

    def _remove(self, http: DtRequest) -> None:
        """Delete rows from the database.

        1. Fires ``'preRemove'`` per row (cancelled rows are tracked).
        2. Runs :meth:`~datatables_server.mjoin.Mjoin.remove` on all Mjoins.
        3. Optionally deletes from left-joined tables (when
           :meth:`left_join_remove` is enabled).
        4. Deletes from each main table.
        5. Fires ``'postRemove'`` per row and ``'postRemoveAll'``.

        Args:
            http: The parsed request containing ``data`` with row IDs.
        """
        if not http.data:
            return

        ids: List[str] = []
        for key in list(http.data.keys()):
            id_ = key.replace(self.id_prefix(), "")
            res = self._trigger("preRemove", id_, http.data[key])
            if res is False:
                self._out.cancelled.append(id_)
            else:
                ids.append(id_)

        if not ids:
            return

        # Remove Mjoin rows first (they depend on the parent row)
        for mj in self._join:
            mj.remove(self, ids)

        # Optionally remove left-joined table rows
        if self._left_join_remove:
            for lj in self._left_join:
                if not lj.field1 or not lj.field2:
                    continue

                table_orig = self._alias(lj.table, "orig")

                # Determine which side references the parent
                if lj.field1.startswith(lj.table):
                    parent_link = lj.field2
                    child_link = lj.field1
                else:
                    parent_link = lj.field1
                    child_link = lj.field2

                if parent_link == self._pkey[0] and len(self._pkey) == 1 and child_link:
                    self._remove_table(lj.table, ids, [child_link])

        # Delete from the primary write tables
        for tbl in self.table():
            self._remove_table(tbl, ids)

        # Post-events
        for id_ in ids:
            self._trigger("postRemove", id_, http.data.get(self.id_prefix() + id_))

        self._trigger("postRemoveAll", ids, http.data)

    def _remove_table(self, table: str, ids: List[str], pkey: List[str] = None) -> None:
        """Delete rows from a specific table.

        Only deletes if at least one field in the table has a set type other
        than :attr:`~datatables_server.field.SetType.NONE` (or no table
        qualification is present), ensuring we do not accidentally delete from
        unrelated tables.

        Args:
            table: Table to delete from.
            ids:   Row primary key values.
            pkey:  Override primary key column list.  Defaults to
                :meth:`pkey`.
        """
        if pkey is None:
            pkey = self.pkey()

        table_alias = self._alias(table, "alias")
        table_orig = self._alias(table, "orig")

        # Replace alias references in pkey with the real table name (for DELETE)
        resolved_pkey = []
        for pk in pkey:
            if pk.startswith(table_alias + "."):
                resolved_pkey.append(pk.replace(table_alias + ".", table_orig + ".", 1))
            else:
                resolved_pkey.append(pk)

        # Count fields applicable to this table
        count = 0
        for field in self.fields():
            db_field = field.db_field()
            if "." not in db_field or (self._part(db_field, "table") == table_alias and field.set() != SetType.NONE):
                count += 1

        if count == 0:
            return

        # Resolve to bare column names for use in the DELETE statement.
        # pkey entries may be 'table.column' or plain 'column'; we only want
        # the column part for both the sa.table() definition and the WHERE.
        bare_pkey = [self._part(pk, "column") if "." in pk else pk for pk in resolved_pkey]

        # Build and execute DELETE with OR'd PK conditions
        or_conds = []
        for id_ in ids:
            # Split the composite id back into its parts and pair with bare column names
            id_stripped = id_.replace(self.id_prefix(), "")
            id_parts = id_stripped.split(self._pkey_separator())
            if len(id_parts) != len(bare_pkey):
                continue
            and_conds = [sa.column(col) == val for col, val in zip(bare_pkey, id_parts)]
            or_conds.append(sa.and_(*and_conds))

        if not or_conds:
            return

        del_tbl = sa.table(table_orig, *[sa.column(c) for c in bare_pkey])
        stmt = sa.delete(del_tbl).where(sa.or_(*or_conds))
        self._exec(stmt)

    def _ssp(self, stmt: sa.Select, http: DtRequest) -> tuple:
        """Apply server-side processing (SSP) to *stmt* and return metadata.

        Modifies *stmt* in place (SQLAlchemy's immutable select means each
        operation returns a new object — we return the final statement).  Also
        runs a separate count query to determine ``recordsTotal`` and
        ``recordsFiltered``.

        Args:
            stmt: The SELECT statement for data retrieval.
            http: The parsed request (provides ``draw``, ``order``, ``start``,
                ``length``, ``search``, ``columns``).

        Returns:
            A ``(modified_stmt, SspResult)`` tuple.  When *http* has no
            ``draw`` value the original statement is returned unchanged with an
            empty :class:`~datatables_server.types.SspResult`.
        """
        if not http or not http.draw:
            return stmt, SspResult()

        # Apply ordering, filtering, and pagination TO the data query
        stmt = self._ssp_sort(stmt, http)
        stmt, is_filtered = self._ssp_filter(stmt, http)
        stmt = self._ssp_limit(stmt, http)

        # ---- Count query (no limit/offset) ----------------------------------
        pkey_col = self._pkey[0]
        count_base = sa.select(sa.func.count(sa.literal_column(pkey_col)).label("cnt")).select_from(
            sa.text(self._read_table()[0])
        )
        count_base = self._get_where(count_base)
        count_base, _ = self._ssp_filter(count_base, http)
        count_base = _apply_left_joins(count_base, self._left_join)

        cnt_result = list(self._exec(count_base))
        records_filtered = int(cnt_result[0].cnt) if cnt_result else 0
        records_total = records_filtered

        # Compute unfiltered total only when a filter is active
        has_where = bool(self._where)
        if has_where or is_filtered:
            total_base = sa.select(sa.func.count(sa.literal_column(pkey_col)).label("cnt")).select_from(
                sa.text(self._read_table()[0])
            )
            total_base = self._get_where(total_base)
            if has_where:
                total_base = _apply_left_joins(total_base, self._left_join)

            tot_result = list(self._exec(total_base))
            records_total = int(tot_result[0].cnt) if tot_result else 0

        return stmt, SspResult(
            draw=int(http.draw),
            records_filtered=records_filtered,
            records_total=records_total,
        )

    def _ssp_field(self, http: DtRequest, index: int) -> str:
        """Resolve the database field name for a given column index.

        Args:
            http:  The parsed request.
            index: Zero-based column index.

        Returns:
            The database field name string.

        Raises:
            ValueError: If no matching field can be found.
        """
        if http.columns and index < len(http.columns):
            col_data = http.columns[index].data
            field = self._find_field(col_data, "name")
            if field:
                return field.db_field()

            if col_data == "DT_RowId":
                return self._pkey[0]

        raise ValueError(f"Unknown SSP field at column index {index}")

    def _ssp_filter(self, stmt: sa.Select, http: DtRequest) -> tuple:
        """Apply search / filter conditions to *stmt*.

        Handles:

        * Global search (applied via ``LIKE`` to all searchable columns).
        * SearchPanes per-field filters.
        * SearchBuilder criteria.
        * ColumnControl search / list filters.
        * Per-column search values.

        Args:
            stmt: The SELECT statement to extend.
            http: The parsed request.

        Returns:
            A ``(modified_stmt, filtered_bool)`` tuple.  The boolean is
            ``True`` when at least one condition was applied.
        """
        if not http:
            return stmt, False

        filtered = False
        fields = self.fields()

        # ---- Global search --------------------------------------------------
        if http.search and http.search.get("value"):
            filtered = True
            search_val = http.search["value"]
            or_conds = []

            if http.columns:
                for i, col in enumerate(http.columns):
                    if str(col.searchable).lower() == "true":
                        try:
                            field_name = self._ssp_field(http, i)
                            or_conds.append(sa.cast(sa.literal_column(field_name), sa.Text).ilike(f"%{search_val}%"))
                        except Exception:
                            pass

            if or_conds:
                stmt = stmt.where(sa.or_(*or_conds))

        # ---- SearchPanes ----------------------------------------------------
        if http.search_panes:
            for field in fields:
                pane_vals = http.search_panes.get(field.name())
                if pane_vals is not None:
                    filtered = True
                    pane_conds = []

                    for i, pv in enumerate(pane_vals):
                        null_panes = http.search_panes_null or {}
                        field_null = null_panes.get(field.name(), [])
                        is_null_filter = i < len(field_null) and field_null[i] != "false"

                        if is_null_filter:
                            pane_conds.append(
                                sa.or_(
                                    sa.literal_column(field.db_field()).is_(None),
                                    sa.literal_column(field.db_field()) == "",
                                )
                            )
                        else:
                            pane_conds.append(sa.literal_column(field.db_field()) == pv)

                    stmt = stmt.where(sa.or_(*pane_conds))

        # ---- SearchBuilder --------------------------------------------------
        if http.search_builder and http.search_builder.get("criteria"):
            from .search_pane_options import construct_search_builder_query

            filtered = True
            stmt = construct_search_builder_query(stmt, http.search_builder)

        # ---- ColumnControl --------------------------------------------------
        stmt = column_control_ssp(self, stmt, http)

        # ---- Per-column search ----------------------------------------------
        if http.columns:
            for i, col in enumerate(http.columns):
                col_search = col.search.get("value", "") if col.search else ""
                if col_search and str(col.searchable).lower() == "true":
                    filtered = True
                    try:
                        field_name = self._ssp_field(http, i)
                        stmt = stmt.where(sa.cast(sa.literal_column(field_name), sa.Text).ilike(f"%{col_search}%"))
                    except Exception:
                        pass

        return stmt, filtered

    def _ssp_limit(self, stmt: sa.Select, http: DtRequest) -> sa.Select:
        """Apply LIMIT / OFFSET for pagination.

        Skips when ``length`` is ``-1`` (DataTables "show all" mode).

        Args:
            stmt: The SELECT statement to extend.
            http: The parsed request.

        Returns:
            The (possibly modified) SELECT statement.
        """
        if http.length is not None and http.start is not None and http.length != -1:
            stmt = stmt.limit(int(http.length)).offset(int(http.start))
        return stmt

    def _ssp_sort(self, stmt: sa.Select, http: DtRequest) -> sa.Select:
        """Apply ORDER BY for server-side processing.

        Falls back to ordering by the first primary key column ascending when
        no ordering is requested.

        Args:
            stmt: The SELECT statement to extend.
            http: The parsed request.

        Returns:
            The (possibly modified) SELECT statement.
        """
        if http.order:
            for order in http.order:
                field_name = self._ssp_field(http, order.column)
                col = sa.literal_column(field_name)
                stmt = stmt.order_by(col.desc() if order.dir == "desc" else col.asc())
        else:
            stmt = stmt.order_by(sa.literal_column(self._pkey[0]).asc())
        return stmt

    def _trigger(self, name: str, *args: Any) -> Any:
        """Fire a named event and collect the return values.

        All registered callbacks receive the :class:`Editor` instance as their
        first argument, followed by the positional *args*.  If any callback
        returns ``False``, the final return value is ``False``; otherwise the
        last non-``None`` return value is returned, defaulting to ``True``.

        Args:
            name: Event name.
            *args: Additional arguments forwarded to every callback.

        Returns:
            ``False`` if any handler returned ``False``; the last non-``None``
            result otherwise; or ``True`` if no handlers are registered.
        """
        if name not in self._events:
            return True

        out = None
        for callback in self._events[name]:
            res = callback(self, *args)
            if res is not None:
                out = res

        return out

    def _update(self, id: str, values: Dict) -> str:
        """Update an existing row.

        1. Strips the ID prefix from *id*.
        2. Fires ``'validatedEdit'``.
        3. Calls :meth:`_insert_or_update`.
        4. Runs :meth:`~datatables_server.mjoin.Mjoin.update` on all Mjoins.
        5. Merges any updated PK values.
        6. Fires ``'writeEdit'``.

        Args:
            id:     Row ID (may include the :meth:`id_prefix`).
            values: Submitted row data.

        Returns:
            The (potentially updated) primary key string.
        """
        id = id.replace(self.id_prefix(), "")
        self._trigger("validatedEdit", id, values)

        self._insert_or_update(id, values)

        for mj in self._join:
            mj.update(self, id, values)

        return self._pkey_submit_merge(id, values)

    def _upload(self, http: DtRequest) -> None:
        """Handle a file upload request.

        Searches for the upload field in the local fields first, then in each
        Mjoin instance.  Delegates to the field's
        :class:`~datatables_server.upload.Upload` instance.

        Args:
            http: The parsed request (provides ``upload_field`` and the file
                data stored in :attr:`_upload_data`).
        """
        if not http.upload_field:
            return

        field = self._find_field(http.upload_field, "name")
        field_name = ""

        if not field:
            # Search in Mjoin instances
            for mj in self._join:
                for jf in mj.fields():
                    candidate_name = f"{mj.name()}[].{jf.name()}"
                    if candidate_name == http.upload_field:
                        field = jf
                        field_name = candidate_name
                        break
                if field:
                    break
        else:
            field_name = field.name()

        if not self._upload_data:
            raise ValueError("No upload data supplied")

        if not field:
            raise ValueError("Unknown upload field name submitted")

        event_res = self._trigger("preUpload", http)
        if event_res is False:
            return

        upload = field.upload()
        if not upload:
            raise ValueError("File uploaded to a field that does not have upload options configured")

        res = upload.exec(self, self._upload_data) or ""

        if not res and self._out.field_errors is not None:
            self._out.field_errors.append(DtError(name=field_name, status=upload.error() or ""))
        else:
            files = self._file_data(upload.table(), [res])
            self._out.files = files
            self._out.upload = {"id": res}
            self._trigger("postUpload", res, files, http, field)

    # ------------------------------------------------------------------
    # Request parsing helper
    # ------------------------------------------------------------------

    def _dict_to_dt_request(self, data: dict) -> DtRequest:
        """Convert a raw HTTP body dict to a :class:`~datatables_server.types.DtRequest`.

        Handles the camelCase → snake_case translation for all standard
        DataTables / Editor fields, as well as nested structures for
        ``columns``, ``order``, ``searchBuilder``, ``searchPanes``, and
        ``columnControl``.

        Args:
            data: The raw dict, typically from ``request.json`` or
                ``json.loads(request.body)``.

        Returns:
            A fully populated :class:`~datatables_server.types.DtRequest`.
        """
        req = DtRequest()
        req.action = data.get("action")
        req.data = data.get("data")
        req.draw = int(data["draw"]) if data.get("draw") is not None else None
        req.field = data.get("field")
        req.ids = data.get("ids")
        req.start = int(data["start"]) if data.get("start") is not None else None
        req.length = int(data["length"]) if data.get("length") is not None else None
        req.search = data.get("search")
        req.search_builder = data.get("searchBuilder")
        req.search_panes = data.get("searchPanes")
        req.search_panes_null = data.get("searchPanes_null")
        req.upload_field = data.get("uploadField")
        req.values = data.get("values")

        # Parse ordering array – supports both a JSON list and a form-encoded
        # index-keyed dict (e.g. {"0": {"column": "1", "dir": "asc"}}).
        if data.get("order"):
            order_raw = data["order"]
            order_items = order_raw.values() if isinstance(order_raw, dict) else order_raw
            req.order = [DtOrder(dir=o.get("dir", "asc"), column=int(o.get("column", 0))) for o in order_items]

        # Parse columns array – same dual-format support.
        if data.get("columns"):
            req.columns = []
            columns_raw = data["columns"]
            columns_items = columns_raw.values() if isinstance(columns_raw, dict) else columns_raw
            for c in columns_items:
                cc_data = c.get("columnControl")
                cc = None
                if cc_data:
                    cc = DtColumnControl(
                        list=cc_data.get("list"),
                        search=cc_data.get("search"),
                    )
                searchable_raw = c.get("searchable", True)
                if isinstance(searchable_raw, str):
                    searchable = searchable_raw.lower() != "false"
                else:
                    searchable = bool(searchable_raw)

                req.columns.append(
                    DtColumn(
                        data=c.get("data", ""),
                        searchable=searchable,
                        search=c.get("search", {"value": ""}),
                        column_control=cc,
                    )
                )

        return req
