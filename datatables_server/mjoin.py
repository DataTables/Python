"""
One-to-many join helper for DataTables Editor.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Union

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from .field import Field, SetType
from .nested_data import NestedData
from .types import DtError, DtResponse, LeftJoin

if TYPE_CHECKING:
    from .editor import Editor

# ---------------------------------------------------------------------------
# Public type alias
# ---------------------------------------------------------------------------

MjoinValidator = Callable[["Editor", str, List[Any]], Union[bool, str]]
"""Callable signature for group-level Mjoin validators.

Receives the :class:`~datatables_server.editor.Editor` instance, the action
string (``'create'`` / ``'edit'`` / ``'remove'``), and the list of submitted
join-row dicts.  Return ``True`` to pass, or a human-readable error string to
fail.
"""


# ---------------------------------------------------------------------------
# Module-level helper
# ---------------------------------------------------------------------------


def _apply_left_joins(stmt: sa.Select, left_joins: List[LeftJoin]) -> sa.Select:
    """Append LEFT JOIN clauses to *stmt*.

    For each :class:`~datatables_server.types.LeftJoin`:

    * If ``fn`` is set the callable receives the statement and must return the
      modified statement.
    * Otherwise a raw ``LEFT JOIN … ON field1 operator field2`` fragment is
      appended via :meth:`~sqlalchemy.sql.selectable.Select.join`.

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


# ---------------------------------------------------------------------------
# Mjoin class
# ---------------------------------------------------------------------------


class Mjoin(NestedData):
    """One-to-many join for DataTables Editor.

    Provides a one-to-many join link between the main Editor table and a
    secondary table.  Useful for cases where an attribute can take multiple
    values simultaneously — for example cumulative security access levels.

    Typically used with a link table (many-to-many), but the link table is
    optional.  **Note**: when no link table is used, on edit the linked rows
    are deleted and re-inserted; any values not in the field list will be lost.

    Example::

        mjoin = (
            Mjoin("user_roles")
            .link("users.id", "user_roles.user_id")
            .fields(
                Field("role_id"),
            )
        )
        editor.join(mjoin)
    """

    # Expose SetType as a convenience class attribute.
    SetType = SetType

    # ------------------------------------------------------------------
    # Constructor
    # ------------------------------------------------------------------

    def __init__(self, table: str) -> None:
        """Create an Mjoin instance.

        Sets both :meth:`table` and :meth:`name` to *table* so the join is
        immediately usable with minimal configuration.

        Args:
            table: The database table name being joined to.
        """
        super().__init__()

        self._table: str = table
        self._editor: Optional["Editor"] = None
        self._name: str = table
        self._get: bool = True
        self._left_join: List[LeftJoin] = []
        self._set: SetType = SetType.BOTH
        self._where: List[Any] = []
        self._fields: List[Field] = []
        self._links: List[str] = []
        self._order: str = ""
        # Populated by _prepare():
        self._join: Dict[str, Any] = {"child": "", "parent": ""}
        self._validators: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Public API — chainable getters / setters
    # ------------------------------------------------------------------

    def field(self, name_or_field: Union[str, Field]) -> Union["Mjoin", Field]:
        """Get a field by name, or add a field instance.

        Args:
            name_or_field: A :class:`~datatables_server.field.Field` instance
                to add, or a field name string to look up.

        Returns:
            The matching :class:`~datatables_server.field.Field` when a string
            is passed (raises ``ValueError`` if not found), or ``self`` for
            chaining when a :class:`~datatables_server.field.Field` is passed.

        Raises:
            ValueError: When *name_or_field* is a string and no field with that
                name is registered.
        """
        if isinstance(name_or_field, str):
            for f in self._fields:
                if f.name() == name_or_field:
                    return f
            raise ValueError(f"Unknown field: {name_or_field}")

        self._fields.append(name_or_field)
        return self

    def fields(self, *fields: Field) -> Union["Mjoin", List[Field]]:
        """Get all registered fields, or add one or more fields.

        Called with no arguments acts as a getter and returns the current field
        list.  Called with one or more :class:`~datatables_server.field.Field`
        arguments appends them and returns ``self`` for chaining.

        Args:
            *fields: Zero or more :class:`~datatables_server.field.Field`
                instances to add.

        Returns:
            The current list of :class:`~datatables_server.field.Field`
            instances (getter), or ``self`` (setter / chaining).
        """
        if not fields:
            return self._fields

        self._fields.extend(fields)
        return self

    def get(self, flag: bool = None) -> Union["Mjoin", bool]:
        """Get or set whether join data is read from the database.

        When ``False`` no ``SELECT`` is performed on the join table during read
        operations.

        Args:
            flag: ``True`` (default) to enable reads; ``False`` to suppress.
                Omit to use as a getter.

        Returns:
            Current boolean value (getter) or ``self`` (setter / chaining).
        """
        if flag is None:
            return self._get

        self._get = flag
        return self

    def left_join(
        self,
        table: str,
        field1_or_fn: Union[str, Callable] = None,
        operator: str = None,
        field2: str = None,
    ) -> "Mjoin":
        """Add a LEFT JOIN to the join query.

        Two call signatures are supported:

        * ``left_join(table, fn)`` — *field1_or_fn* is a callable that receives
          the current SELECT statement and must return a modified statement.
        * ``left_join(table, field1, operator, field2)`` — plain field join
          using the given operator (e.g. ``'='``).

        Args:
            table:        Name of the table to join onto.
            field1_or_fn: Left-hand field name or a statement-mutating callable.
            operator:     Comparison operator (e.g. ``'='``).
            field2:       Right-hand field name.

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

    def link(self, field1: str, field2: str) -> "Mjoin":
        """Create a join link between two tables using ``'table.column'`` notation.

        This method can be called at most **twice** for a given instance:

        * **First call** — links the Editor host table to the join table (or
          to the link table in a many-to-many setup).
        * **Second call** — links the link table to the target table
          (many-to-many only).

        The actual resolution of which side is which is deferred until query
        time via :meth:`_prepare`.

        Args:
            field1: ``'table.column'`` reference for the left side.
            field2: ``'table.column'`` reference for the right side.

        Returns:
            ``self`` for method chaining.

        Raises:
            ValueError: If either argument lacks a ``'.'`` separator.
            ValueError: If this method has already been called twice.
        """
        if "." not in field1 or "." not in field2:
            raise ValueError("Mjoin fields must contain both the table name and the column name")

        if len(self._links) == 4:
            raise ValueError("Mjoin link() cannot be called more than twice for a single instance")

        self._links.append(field1)
        self._links.append(field2)
        return self

    def name(self, name: str = None) -> Union["Mjoin", str]:
        """Get or set the name used for this join in JSON output and HTTP input.

        The name is used as the JSON property key when reading data, and as the
        HTTP form-field prefix when writing data.  Defaults to the table name
        supplied to the constructor.

        Args:
            name: New name string.  Omit to use as a getter.

        Returns:
            Current name string (getter) or ``self`` (setter / chaining).
        """
        if name is None:
            return self._name

        self._name = name
        return self

    def order(self, order: str = None) -> Union["Mjoin", str]:
        """Get or set the ``ORDER BY`` column for the join query results.

        The value is a raw SQL expression such as ``'role_name asc'``.

        Args:
            order: SQL ``ORDER BY`` expression.  Omit to use as a getter.

        Returns:
            Current order string (getter) or ``self`` (setter / chaining).
        """
        if order is None:
            return self._order

        self._order = order
        return self

    def set(self, flag: Union[bool, SetType] = None) -> Union["Mjoin", SetType]:
        """Get or set when the join data is written to the database.

        Mirrors the behaviour of :meth:`~datatables_server.field.Field.set`.

        Args:
            flag: ``True`` → :attr:`~datatables_server.field.SetType.BOTH`,
                ``False`` → :attr:`~datatables_server.field.SetType.NONE`,
                or a :class:`~datatables_server.field.SetType` enum value.
                Omit to use as a getter.

        Returns:
            Current :class:`~datatables_server.field.SetType` (getter) or
            ``self`` (setter / chaining).
        """
        if flag is None:
            return self._set

        if flag is True:
            self._set = SetType.BOTH
        elif flag is False:
            self._set = SetType.NONE
        else:
            self._set = flag

        return self

    def table(self, table: str = None) -> Union["Mjoin", str]:
        """Get or set the join table name.

        Note: setting the table via the constructor also sets :meth:`name`.
        This setter **only** updates the table and does not change the name.

        Args:
            table: Table name.  Omit to use as a getter.

        Returns:
            Current table name (getter) or ``self`` (setter / chaining).
        """
        if table is None:
            return self._table

        self._table = table
        return self

    def validator(self, field_name: str, fn: MjoinValidator) -> "Mjoin":
        """Add a group-level validator for the array of submitted join data.

        Unlike field-level validators which run on individual rows, group-level
        validators receive the entire submitted join data array and can enforce
        cross-row rules (e.g. minimum/maximum row counts).

        Args:
            field_name: The field name whose error slot on the client-side will
                display the error message if validation fails.
            fn: Callable with signature
                ``(editor, action, data) -> True | str``.  Return ``True`` to
                pass, or a human-readable string to fail.

        Returns:
            ``self`` for method chaining.
        """
        self._validators.append({"field_name": field_name, "fn": fn})
        return self

    def where(self, cond: Any = None) -> Union["Mjoin", List[Any]]:
        """Get or append a WHERE condition for the join query.

        Conditions are applied to the child table.  Each call appends an
        additional condition; all conditions are combined with ``AND``.

        Args:
            cond: A SQLAlchemy WHERE expression (e.g. a column comparison).
                Omit to use as a getter.

        Returns:
            Current list of conditions (getter) or ``self`` (setter / chaining).
        """
        if cond is None:
            return self._where

        self._where.append(cond)
        return self

    # ------------------------------------------------------------------
    # Internal methods called by Editor
    # ------------------------------------------------------------------

    def data(self, editor: "Editor", response: DtResponse) -> None:
        """Read join data from the database and attach it to *response*.

        Called by :meth:`~datatables_server.editor.Editor._get` after the main
        query has executed.  For each row already in ``response.data`` this
        method reads the related join rows and stores them under
        ``row[self.name()]``.

        Args:
            editor:   The owning :class:`~datatables_server.editor.Editor`.
            response: The response object being built; ``response.data`` is
                modified in-place.
        """
        if not self._get:
            return

        self._prepare(editor)
        fields = self.fields()
        join = self._join

        if editor.pkey() and len(editor.pkey()) > 1:
            raise ValueError("Mjoin is not currently supported with a compound primary key for the main table")

        if not response.data:
            return

        dte_table = editor.table()[0]
        join_field = join["table"] if join.get("table") else None
        join_parent: Any = join["parent"]
        join_child: Any = join["child"]

        # Resolve the parent join field name (first element if link table, else the string itself)
        effective_parent = join_parent[0] if join.get("table") else join_parent

        # Compute table aliases
        if " " in dte_table:
            dte_table_alias = re.split(r" (as )?", dte_table, flags=re.IGNORECASE)[2]
        else:
            dte_table_alias = dte_table

        mjoin_table_raw = self._table
        if " " in mjoin_table_raw:
            parts = re.split(r" (as )?", mjoin_table_raw, flags=re.IGNORECASE)
            m_join_table = parts[0]
            m_join_table_alias = parts[2]
        else:
            m_join_table = mjoin_table_raw
            m_join_table_alias = mjoin_table_raw

        # Determine whether the pkey IS the join field (so we can look it up from DT_RowId)
        pkey_is_join = effective_parent == editor.pkey()[0] or (
            dte_table_alias + "." + effective_parent == editor.pkey()[0]
        )

        # ------------------------------------------------------------------
        # Build the SELECT: dte_table_alias.join_field AS dteditor_pkey + all join fields
        # ------------------------------------------------------------------
        pkey_col_expr = f"{dte_table_alias}.{effective_parent}"
        select_cols = [sa.literal_column(pkey_col_expr).label("dteditor_pkey")]

        for f in fields:
            if f.apply("get") and f.get_value() is None and not f._get_value_set:
                db_field = f.db_field()
                if "(" in db_field:
                    # Function expression — use as-is
                    select_cols.append(sa.literal_column(db_field).label(db_field))
                elif "." in db_field:
                    # Already table-qualified
                    select_cols.append(sa.literal_column(db_field).label(db_field))
                else:
                    # Bare column — prefix with join table alias
                    select_cols.append(sa.literal_column(f"{m_join_table_alias}.{db_field}").label(db_field))

        stmt = sa.select(*select_cols).select_from(sa.text(dte_table))

        # Apply ORDER BY
        if self._order:
            parts = self._order.split()
            if len(parts) >= 2:
                direction = parts[-1].lower()
                col_name = " ".join(parts[:-1])
                if direction == "desc":
                    stmt = stmt.order_by(sa.literal_column(col_name).desc())
                else:
                    stmt = stmt.order_by(sa.literal_column(col_name).asc())
            else:
                stmt = stmt.order_by(sa.literal_column(self._order))

        # ------------------------------------------------------------------
        # Build JOINs
        # ------------------------------------------------------------------
        if join.get("table"):
            # Many-to-many via link table
            link_table: str = join["table"]
            parent_cols: List[str] = join["parent"]  # [editor_col, link_col]
            child_cols: List[str] = join["child"]  # [target_col, link_col]

            # editor_table → link_table
            stmt = stmt.join(
                sa.text(link_table),
                sa.text(f"{dte_table_alias}.{parent_cols[0]} = {link_table}.{parent_cols[1]}"),
            )
            # link_table → target_table (aliased)
            stmt = stmt.join(
                sa.text(f"{m_join_table} AS {m_join_table_alias}"),
                sa.text(f"{m_join_table_alias}.{child_cols[0]} = {link_table}.{child_cols[1]}"),
            )
        else:
            # Direct one-to-many join
            stmt = stmt.join(
                sa.text(f"{m_join_table} AS {m_join_table_alias}"),
                sa.text(f"{m_join_table_alias}.{join_child} = {dte_table_alias}.{join_parent}"),
            )

        # Apply additional LEFT JOINs
        stmt = _apply_left_joins(stmt, self._left_join)

        # Apply stored WHERE conditions
        for cond in self._where:
            stmt = stmt.where(cond)

        # ------------------------------------------------------------------
        # Determine which field on the response rows contains the parent PK
        # ------------------------------------------------------------------
        read_field: str = ""
        first_row = response.data[0]

        if self._prop_exists(f"{dte_table_alias}.{effective_parent}", first_row):
            read_field = f"{dte_table_alias}.{effective_parent}"
        elif self._prop_exists(str(effective_parent), first_row):
            read_field = str(effective_parent)
        elif not pkey_is_join:
            raise ValueError(
                f'Join was performed on the field "{effective_parent}" which was not '
                "included in the Editor field list. The join field must be included as a "
                "regular field in the Editor instance."
            )

        # ------------------------------------------------------------------
        # WHERE IN optimisation — only for "sensible" data set sizes (<1000)
        # ------------------------------------------------------------------
        if len(response.data) < 1000:
            id_prefix = editor.id_prefix()
            where_in_vals = []
            for row in response.data:
                if pkey_is_join:
                    val = str(row.get("DT_RowId", "")).replace(id_prefix, "")
                else:
                    val = self._read_prop(read_field, row)
                where_in_vals.append(val)

            stmt = stmt.where(sa.literal_column(pkey_col_expr).in_(where_in_vals))

        # ------------------------------------------------------------------
        # Execute and build join map: pkey_value -> [row_dict, …]
        # ------------------------------------------------------------------
        db: Connection = editor.db()
        result = db.execute(stmt)
        rows = [dict(r._mapping) for r in result]

        join_map: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows:
            inner: Dict[str, Any] = {}
            for f in fields:
                f.write(inner, row)

            lookup = str(row.get("dteditor_pkey", ""))
            if lookup not in join_map:
                join_map[lookup] = []
            join_map[lookup].append(inner)

        # ------------------------------------------------------------------
        # Attach join data to each response row
        # ------------------------------------------------------------------
        id_prefix = editor.id_prefix()
        for row in response.data:
            if pkey_is_join:
                link_val = str(row.get("DT_RowId", "")).replace(id_prefix, "")
            else:
                link_val = str(self._read_prop(read_field, row) or "")

            row[self._name] = join_map.get(link_val, [])

    def options(self, options: Dict, db: Connection, refresh: bool) -> None:
        """Populate *options* with any option lists defined on the join's fields.

        Called by :meth:`~datatables_server.editor.Editor._options`.  For each
        field that has an :class:`~datatables_server.options.Options` instance
        attached, the options are fetched and stored in *options* under the key
        ``'joinName[].<fieldName>'``.

        Args:
            options: The options dict being built (mutated in-place).
            db:      Active database connection.
            refresh: ``True`` when called after a write operation.
        """
        for field in self.fields():
            opts_inst = field.options()
            if opts_inst:
                opts = opts_inst.exec(db, refresh)
                if opts is not False and opts is not None:
                    key = f"{self.name()}[].{field.name()}"
                    options[key] = opts

    def create(self, editor: "Editor", parent_id: str, data: Any) -> None:
        """Insert join rows for a newly created parent row.

        Only runs when :meth:`set` is :attr:`~datatables_server.field.SetType.CREATE`
        or :attr:`~datatables_server.field.SetType.BOTH`, and when the submitted
        data includes the join name key and its ``'-many-count'`` companion.

        Args:
            editor:    The owning :class:`~datatables_server.editor.Editor`.
            parent_id: The primary key value of the newly created parent row.
            data:      The submitted row data dict.
        """
        if self._set not in (SetType.CREATE, SetType.BOTH):
            return

        if not data.get(self._name) or not data.get(f"{self._name}-many-count"):
            return

        self._prepare(editor)
        db = editor.db()

        for row in data[self._name]:
            self._insert(db, parent_id, row)

    def update(self, editor: "Editor", parent_id: str, data: Any) -> None:
        """Update join rows for an edited parent row (delete + recreate).

        Only runs when :meth:`set` is :attr:`~datatables_server.field.SetType.EDIT`
        or :attr:`~datatables_server.field.SetType.BOTH`, and when the submitted
        data includes the ``'-many-count'`` companion key (even if empty, to
        distinguish "no change" from "clear all").

        **Warning**: Any data in the join table that is not covered by the field
        list will be lost on update.

        Args:
            editor:    The owning :class:`~datatables_server.editor.Editor`.
            parent_id: The primary key value of the parent row being edited.
            data:      The submitted row data dict.
        """
        if self._set not in (SetType.EDIT, SetType.BOTH):
            return

        # '-many-count' key must exist (even if the array is empty)
        if f"{self._name}-many-count" not in data:
            return

        # Delete all existing join rows, then re-insert the submitted ones
        self.remove(editor, [parent_id])
        self.create(editor, parent_id, data)

    def remove(self, editor: "Editor", ids: List[str]) -> None:
        """Delete join rows for the given parent IDs.

        Called by :meth:`~datatables_server.editor.Editor._remove` before
        deleting the parent rows to avoid orphaned data.

        Args:
            editor: The owning :class:`~datatables_server.editor.Editor`.
            ids:    List of parent primary key values to delete join rows for.
        """
        if not self._set:
            return

        self._prepare(editor)
        db: Connection = editor.db()
        join = self._join

        if join.get("table"):
            # Delete from the link table
            link_table: str = join["table"]
            parent_col: str = join["parent"][1]  # link-table side of parent link
            conditions = [sa.column(parent_col) == id_ for id_ in ids]
            stmt = sa.delete(sa.table(link_table, sa.column(parent_col))).where(sa.or_(*conditions))
            db.execute(stmt)
        else:
            # Delete directly from the target table
            child_col: str = str(join["child"])
            conditions = [sa.column(child_col) == id_ for id_ in ids]
            stmt = sa.delete(sa.table(self._table, sa.column(child_col))).where(sa.or_(*conditions))
            # Apply any additional WHERE conditions
            for cond in self._where:
                stmt = stmt.where(cond)
            db.execute(stmt)

    def validate(self, errors: List[DtError], editor: "Editor", data: Any, action: str) -> None:
        """Validate submitted join data, running group- and field-level validators.

        Group-level validators (registered via :meth:`validator`) run first.
        Then each submitted join row is validated against the field-level
        validators.  All errors are appended to *errors*.

        Skips entirely when:

        * :meth:`set` is falsy (no writes configured).
        * *action* is ``'edit'`` and the ``'-many-count'`` companion key was
          not submitted (meaning "no change").

        Args:
            errors: List to append :class:`~datatables_server.types.DtError`
                instances to.
            editor: The owning :class:`~datatables_server.editor.Editor`.
            data:   The submitted row data dict for the parent row.
            action: Current action string (``'create'`` / ``'edit'``).
        """
        if not self._set:
            return

        self._prepare(editor)

        join_data: List[Any] = data.get(self._name, [])
        submitted_count = data.get(f"{self._name}-many-count", None)

        # On edit, an absent many-count means "do nothing"
        if action == "edit" and submitted_count is None:
            return

        # Group-level validators
        for v in self._validators:
            res = v["fn"](editor, action, join_data)
            if isinstance(res, str):
                errors.append(DtError(name=v["field_name"], status=res))

        # Field-level validators on each submitted join row
        for row in join_data:
            self._validate_fields(errors, editor, row, f"{self._name}[].", action)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _apply_where(self, stmt: sa.Select) -> sa.Select:
        """Apply all stored WHERE conditions to *stmt*.

        Args:
            stmt: The SELECT statement to extend.

        Returns:
            The modified statement.
        """
        for cond in self._where:
            stmt = stmt.where(cond)
        return stmt

    def _insert(self, db: Connection, parent_id: str, data: Any) -> None:
        """Insert a single join row.

        When a link table is configured, inserts into the link table.
        Otherwise inserts directly into the target table with all writable
        fields.

        Args:
            db:        Active database connection.
            parent_id: Parent row primary key value.
            data:      Submitted data for this join row.
        """
        join = self._join
        fields = self.fields()

        if join.get("table"):
            # Many-to-many: insert parent/child FK pair into link table
            link_table: str = join["table"]
            parent_col: str = join["parent"][1]
            child_col: str = join["child"][1]
            child_val_key: str = join["child"][0]

            tbl = sa.table(
                link_table,
                sa.column(parent_col),
                sa.column(child_col),
            )
            stmt = sa.insert(tbl).values({parent_col: parent_id, child_col: data.get(child_val_key)})
            db.execute(stmt)
        else:
            # One-to-many: insert all writable fields plus the FK back to parent
            child_col = str(join["child"])
            set_vals: Dict[str, Any] = {child_col: parent_id}

            for f in fields:
                if f.apply("create", data):
                    set_vals[f.db_field()] = f.val("set", data)

            col_objs = [sa.column(c) for c in set_vals.keys()]
            tbl = sa.table(self._table, *col_objs)
            stmt = sa.insert(tbl).values(set_vals)
            db.execute(stmt)

    def _prepare(self, editor: "Editor") -> None:
        """Resolve ``self._join`` from ``self._links`` and the editor configuration.

        Must be called before any query is executed.  Idempotent — calling it
        multiple times is safe.

        The method inspects the ``_links`` list (populated by repeated calls to
        :meth:`link`) to determine:

        * With 2 links (no link table): the ``parent`` and ``child`` column names.
        * With 4 links (link table): the link table name plus the ``parent``
          and ``child`` column pairs.

        Args:
            editor: The owning :class:`~datatables_server.editor.Editor`.

        Raises:
            ValueError: If ``_links`` has an unexpected length.
        """
        self._editor = editor

        links = self._links
        editor_table = editor.table()[0]
        join_table = self.table()

        # Resolve the alias (the name used in queries) for the editor table
        if " " in editor_table:
            dte_table_alias = re.split(r" (as )?", editor_table, flags=re.IGNORECASE)[2]
        else:
            dte_table_alias = editor_table

        if len(links) == 2:
            # Simple one-to-many — no link table
            f1 = links[0].split(".")
            f2 = links[1].split(".")

            if f1[0] == dte_table_alias:
                self._join["parent"] = f1[1]
                self._join["child"] = f2[1]
            else:
                self._join["parent"] = f2[1]
                self._join["child"] = f1[1]

        elif len(links) == 4:
            # Many-to-many via link table
            f1 = links[0].split(".")
            f2 = links[1].split(".")
            f3 = links[2].split(".")
            f4 = links[3].split(".")

            # Discover the link table name — it is neither the editor table nor
            # the join (target) table
            link_tbl = None
            for f in (f1, f2, f3, f4):
                if f[0] != dte_table_alias and f[0] != join_table:
                    link_tbl = f[0]
                    break

            self._join["table"] = link_tbl
            self._join["parent"] = [f1[1], f2[1]]
            self._join["child"] = [f3[1], f4[1]]

        else:
            raise ValueError(
                f"Mjoin._prepare: unexpected number of links ({len(links)}). " "Call link() once or twice."
            )

    def _validate_fields(
        self,
        errors: List[DtError],
        editor: "Editor",
        data: Any,
        prefix: str,
        action: str,
    ) -> None:
        """Run field-level validators for a single join row.

        Args:
            errors: List to append :class:`~datatables_server.types.DtError`
                instances to.
            editor: The owning :class:`~datatables_server.editor.Editor`.
            data:   Submitted data for this join row.
            prefix: Prefix to prepend to field names in error objects
                (e.g. ``'roles[].'``).
            action: Current action string (``'create'`` / ``'edit'``).
        """
        for f in self.fields():
            result = f.validate(data, editor, "", action)
            if result is not True:
                errors.append(DtError(name=f"{prefix}{f.name()}", status=str(result)))
