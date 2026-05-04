"""
The :class:`Options` class configures how ``select``, ``radio``, and ``checkbox``
field options are retrieved from a database for the DataTables / Editor server-side
library.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Union

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from .types import LeftJoin

# ---------------------------------------------------------------------------
# Public type aliases
# ---------------------------------------------------------------------------

IOption = Dict[str, Any]
"""A single option dict with at least ``'label'`` and ``'value'`` keys."""

IRenderer = Callable[[Dict[str, Any]], str]
"""A callable that receives a raw database row dict and returns a label string."""

CustomOptions = Callable[[Connection, str], List[IOption]]
"""A callable ``(db, search) -> [IOption]`` used as a fully custom options source."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_numeric(value: Any) -> bool:
    """Return ``True`` if *value* can be interpreted as a finite number.

    Args:
        value: Any value to test.

    Returns:
        ``True`` when *value* is a finite number or a string representation of one.
    """
    try:
        f = float(value)
        return f == f and f not in (float("inf"), float("-inf"))
    except (TypeError, ValueError):
        return False


def _apply_left_joins(stmt: sa.Select, table: str, left_joins: List[LeftJoin]) -> sa.Select:
    """Apply a list of :class:`~datatables_server.types.LeftJoin` entries to *stmt*.

    For each join entry, if a callable ``fn`` is provided it receives the current
    statement and must return the modified statement.  Otherwise, a raw SQL
    ``LEFT JOIN … ON …`` clause is built from ``table``, ``field1``, ``operator``,
    and ``field2``.

    Args:
        stmt:       The SQLAlchemy :class:`~sqlalchemy.sql.selectable.Select` to
                    augment.
        table:      The primary table name (used as the left-hand side of plain
                    ``outerjoin`` calls).
        left_joins: Ordered list of :class:`~datatables_server.types.LeftJoin`
                    descriptors to apply.

    Returns:
        The modified select statement.
    """
    for lj in left_joins:
        if lj.fn:
            stmt = lj.fn(stmt)
        else:
            stmt = stmt.select_from(
                sa.text(table).outerjoin(
                    sa.text(lj.table),
                    sa.text(f"{lj.field1} {lj.operator} {lj.field2}"),
                )
            )
    return stmt


# ---------------------------------------------------------------------------
# Options class
# ---------------------------------------------------------------------------


class Options:
    """Configure how options are retrieved for ``select``, ``radio``, or ``checkbox`` fields.

    This is a port of the TypeScript ``Options`` class from ``options.ts``.  Instances
    are built via a fluent chainable API and then executed with :meth:`exec`.

    Typical usage::

        opts = (
            Options("countries", "id", "name")
            .where(lambda stmt: stmt.where(sa.column("active") == 1))
            .order("name asc")
        )
        result = opts.exec(db, refresh=True)
    """

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(
        self,
        table: Optional[Union[str, CustomOptions]] = None,
        value: Optional[str] = None,
        label: Optional[str] = None,
        fn: Optional[CustomOptions] = None,
    ) -> None:
        """Create an :class:`Options` instance.

        Three call patterns are supported (mirroring the TypeScript overloads):

        * ``Options()`` — bare instance configured entirely via the chaining API.
        * ``Options(table, value, label)`` — shorthand for the most common case.
        * ``Options(fn)`` — delegate option retrieval to *fn* entirely.

        Args:
            table:  Either the database table name *or* a :data:`CustomOptions`
                    callable.  When a callable is passed the remaining positional
                    arguments are ignored.
            value:  Value column name (used when *table* is a string).
            label:  Label column name (used when *table* is a string).
            fn:     Explicit custom-function keyword argument alternative.
        """
        self._always_refresh: bool = True
        self._custom_fn: Optional[CustomOptions] = None
        self._get: bool = True
        self._includes: List[str] = []
        self._search_only: bool = False
        self._table: Optional[str] = None
        self._value: str = ""
        self._label: List[str] = []
        self._left_join: List[LeftJoin] = []
        self._limit: Optional[int] = None
        self._renderer: Optional[IRenderer] = None
        self._where: Any = None
        self._order: Union[str, bool] = True
        self._manual_opts: List[IOption] = []

        if callable(table):
            self._custom_fn = table
        elif isinstance(table, str):
            self._table = table
            if value is not None:
                self._value = value
            if label is not None:
                self._label = label if isinstance(label, list) else [label]
        if fn is not None:
            self._custom_fn = fn

    # ------------------------------------------------------------------
    # Chainable configuration API
    # ------------------------------------------------------------------

    def add(self, label: str, value: Optional[str] = None) -> "Options":
        """Add a manual option to the list, in addition to any DB-sourced options.

        The option will be appended *after* database rows are fetched, so it
        participates in the in-process filtering and limit logic of :meth:`exec`.

        Args:
            label: Human-readable label string.
            value: Option value.  Defaults to *label* when omitted (mirrors the
                   JS behaviour of ``add(label, value = label)``).

        Returns:
            ``self`` for method chaining.
        """
        if value is None:
            value = label
        self._manual_opts.append({"label": label, "value": value})
        return self

    def always_refresh(self, set: Optional[bool] = None) -> Union["Options", bool]:
        """Get or set whether options are refreshed on every operation.

        When ``True`` (the default) options are re-queried on every Editor
        action.  When ``False`` they are only retrieved on the initial data
        load (i.e. when *refresh* is ``False`` in :meth:`exec`).

        Args:
            set: New flag value.  Omit to read the current value.

        Returns:
            The current flag when called with no argument; ``self`` otherwise.
        """
        if set is None:
            return self._always_refresh
        self._always_refresh = set
        return self

    def fn(self, set: Optional[CustomOptions] = None) -> Union["Options", Optional[CustomOptions]]:
        """Get or set a custom function used to retrieve options.

        When a custom function is configured it completely replaces the built-in
        DB query.  The function receives ``(db: Connection, search: str)`` and
        must return a list of :data:`IOption` dicts.

        Args:
            set: New custom function.  Omit to read the current value.

        Returns:
            The current function when called with no argument; ``self`` otherwise.
        """
        if set is None:
            return self._custom_fn
        self._custom_fn = set
        return self

    def get(self, set: Optional[bool] = None) -> Union["Options", bool]:
        """Get or set whether these options are enabled.

        When ``False`` :meth:`exec` returns ``False`` immediately and no DB
        query is performed.

        Args:
            set: New enablement flag.  Omit to read the current value.

        Returns:
            The current flag when called with no argument; ``self`` otherwise.
        """
        if set is None:
            return self._get
        self._get = set
        return self

    def include(self, set: Optional[Union[str, List[str]]] = None) -> Union["Options", List[str]]:
        """Get or set additional columns to include in each option object.

        Named columns are copied from the raw database row into the output
        :data:`IOption` dict alongside ``'label'`` and ``'value'``.

        Args:
            set: A column name or list of column names to append.  Omit to
                 read the current list.

        Returns:
            The current include list when called with no argument; ``self``
            otherwise.
        """
        if set is None:
            return self._includes
        if isinstance(set, list):
            self._includes.extend(set)
        else:
            self._includes.append(set)
        return self

    def label(self, label: Optional[Union[str, List[str]]] = None) -> Union["Options", List[str]]:
        """Get or set the label column(s).

        Multiple columns may be specified; they are joined with a single space
        when the default renderer is used.

        Args:
            label: A column name or list of column names.  Omit to read the
                   current value.

        Returns:
            The current label list when called with no argument; ``self``
            otherwise.
        """
        if label is None:
            return self._label
        if isinstance(label, list):
            self._label = label
        else:
            self._label = [label]
        return self

    def left_join(
        self,
        table: str,
        field1_or_fn: Union[str, Callable],
        operator: Optional[str] = None,
        field2: Optional[str] = None,
    ) -> "Options":
        """Add a LEFT JOIN to the options query.

        Two call signatures are supported:

        * ``left_join(table, fn)`` — *field1_or_fn* is a callable that accepts
          the current :class:`~sqlalchemy.sql.selectable.Select` statement and
          returns a modified one (used for complex join conditions).
        * ``left_join(table, field1, operator, field2)`` — plain equality-style
          join.

        Args:
            table:        Name of the table to join.
            field1_or_fn: Either the left-hand field name or a callable that
                          mutates the select statement.
            operator:     Comparison operator (e.g. ``'='``).  Required for the
                          plain-field form.
            field2:       Right-hand field name.  Required for the plain-field
                          form.

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

    def limit(self, limit: Optional[int] = None) -> Union["Options", Optional[int]]:
        """Get or set a LIMIT on the number of options returned.

        The limit is applied in Python-space after rendering and filtering, not
        in SQL — this ensures it interacts correctly with search and custom
        function sources.

        Args:
            limit: Maximum number of options to return.  Omit to read the
                   current value.

        Returns:
            The current limit when called with no argument; ``self`` otherwise.
        """
        if limit is None:
            return self._limit
        self._limit = limit
        return self

    def order(self, order: Optional[Union[str, bool]] = None) -> Union["Options", Union[str, bool]]:
        """Get or set ORDER BY behaviour.

        * ``True`` (default) — sort in Python by the rendered label string
          (numerically when both sides are numeric, alphabetically otherwise).
        * ``False`` — do not sort; use the database result order.
        * A string, e.g. ``'name asc'`` or ``'updated_at desc, name asc'`` —
          passed through to the SQL ``ORDER BY`` clause.

        Args:
            order: New ordering specification.  Omit to read the current value.

        Returns:
            The current order spec when called with no argument; ``self``
            otherwise.
        """
        if order is None:
            return self._order
        self._order = order
        return self

    def render(self, fn: Optional[IRenderer] = None) -> Union["Options", Optional[IRenderer]]:
        """Get or set a label renderer function.

        The renderer receives the raw database row dict and returns the string
        to use as the option label.  When not set a default renderer that joins
        all :meth:`label` columns with a space is used.

        Args:
            fn: Renderer callable.  Omit to read the current value.

        Returns:
            The current renderer when called with no argument; ``self``
            otherwise.
        """
        if fn is None:
            return self._renderer
        self._renderer = fn
        return self

    def search_only(self, set: Optional[bool] = None) -> Union["Options", bool]:
        """Get or set whether options are only retrieved during a search.

        When ``True`` :meth:`exec` returns ``False`` unless a *search* term or
        *find* IDs are explicitly provided.

        Args:
            set: New flag value.  Omit to read the current value.

        Returns:
            The current flag when called with no argument; ``self`` otherwise.
        """
        if set is None:
            return self._search_only
        self._search_only = set
        return self

    def table(self, table: Optional[str] = None) -> Union["Options", Optional[str]]:
        """Get or set the database table name.

        Args:
            table: Table name.  Omit to read the current value.

        Returns:
            The current table name when called with no argument; ``self``
            otherwise.
        """
        if table is None:
            return self._table
        self._table = table
        return self

    def value(self, value: Optional[str] = None) -> Union["Options", str]:
        """Get or set the value column name.

        This column is typically the primary key of the options table and is
        used as the submitted value for the field.

        Args:
            value: Column name.  Omit to read the current value.

        Returns:
            The current column name when called with no argument; ``self``
            otherwise.
        """
        if value is None:
            return self._value
        self._value = value
        return self

    def where(self, where: Any = None) -> Union["Options", Any]:
        """Get or set a WHERE condition for the options query.

        Two forms are accepted:

        * A **callable** ``(stmt: Select) -> Select`` — called with the current
          :class:`~sqlalchemy.sql.selectable.Select` and must return a modified
          one (use ``stmt.where(...)`` inside).
        * A **dict** ``{column: value, …}`` — converted to equality conditions
          with ``AND`` logic.

        Args:
            where: WHERE specification.  Omit to read the current value.

        Returns:
            The current where spec when called with no argument; ``self``
            otherwise.
        """
        if where is None:
            return self._where
        self._where = where
        return self

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def exec(
        self,
        db: Connection,
        refresh: bool,
        search: Optional[str] = None,
        find: Optional[List[Any]] = None,
    ) -> Union[List[IOption], bool]:
        """Execute the options query and return a formatted list of options.

        Short-circuits to ``False`` in the following situations (mirroring the
        TypeScript logic):

        * :meth:`get` is ``False``.
        * :meth:`search_only` is ``True`` and neither *search* nor *find* were
          provided.
        * *refresh* is ``True`` and :meth:`always_refresh` is ``False``.

        When a :data:`CustomOptions` function is configured it is called instead
        of the built-in DB query.

        Args:
            db:      SQLAlchemy :class:`~sqlalchemy.engine.Connection`.
            refresh: ``True`` when this is a "refresh" call (create/edit/delete
                     response); ``False`` on the initial data load.
            search:  Optional search term; options whose rendered label does
                     **not** start with *search* (case-insensitive) are excluded.
            find:    Optional list of specific values to look up.

        Returns:
            A list of :data:`IOption` dicts, or ``False`` when disabled.
        """
        if not self._get:
            return False

        if self._search_only and search is None and find is None:
            return False

        if refresh and not self._always_refresh:
            return False

        if self._custom_fn is not None:
            return self._custom_fn(db, search or "")

        label_cols = self._label
        value_col = self._value
        formatter = self._renderer

        # Default renderer: join all label columns with a space
        if formatter is None:
            label_cols_snapshot = list(label_cols)

            def formatter(row: Dict[str, Any]) -> str:  # type: ignore[misc]
                return " ".join(str(row[col]) for col in label_cols_snapshot if row.get(col) is not None)

        # Fetch raw rows from the database
        raw_rows: List[Dict[str, Any]] = self.exec_db(db, find)

        # Append any manually added options
        raw_rows.extend(self._manual_opts)

        out: List[IOption] = []
        max_results = self._limit

        for row in raw_rows:
            row_label = formatter(row)
            row_value = row.get(value_col)

            # Apply the search filter in Python-space (rendered label must start with term)
            if search is not None and search != "":
                if not row_label.lower().startswith(search.lower()):
                    continue

            option: IOption = {"label": row_label, "value": row_value}

            # Carry through any requested extra columns
            for inc in self._includes:
                if inc in row:
                    option[inc] = row[inc]

            out.append(option)

            # Limit is enforced in Python-space so it works with search and custom fns
            if max_results is not None and len(out) >= max_results:
                break

        # Sort in Python-space when order=True
        if self._order is True:

            def _sort_key(opt: IOption):
                lbl = opt["label"]
                if lbl is None:
                    lbl = ""
                if _is_numeric(lbl):
                    return (0, float(lbl), "")
                return (1, 0.0, str(lbl))

            out.sort(key=_sort_key)

        return out

    def exec_db(self, db: Connection, find: Optional[List[Any]] = None) -> List[Dict[str, Any]]:
        """Execute the raw database query and return unformatted rows.

        Builds a ``SELECT DISTINCT`` statement that covers all :meth:`label`
        and :meth:`value` columns, applies any :meth:`where` condition, optional
        ``IN`` filter (*find*), LEFT JOINs, and ORDER BY clause.

        Args:
            db:   SQLAlchemy :class:`~sqlalchemy.engine.Connection`.
            find: Optional list of values to filter by (``WHERE value IN (…)``).

        Returns:
            A list of raw row dicts.  Returns an empty list when no table has
            been configured.
        """
        if not self._table:
            return []

        # Collect all columns needed — label columns plus value column (deduplicated)
        cols_to_select: List[str] = list(dict.fromkeys(self._label + [self._value]))

        stmt = sa.select(*[sa.column(c) for c in cols_to_select]).select_from(sa.text(self._table)).distinct()

        # Apply WHERE condition
        if self._where is not None:
            if callable(self._where):
                stmt = self._where(stmt)
            elif isinstance(self._where, dict):
                for k, v in self._where.items():
                    stmt = stmt.where(sa.column(k) == v)

        # Apply value IN filter (used by find() / exec() with explicit IDs)
        if find is not None and len(find) > 0:
            stmt = stmt.where(sa.column(self._value).in_(find))

        # Apply ORDER BY in SQL (allows database-side limiting)
        if isinstance(self._order, str):
            for part in self._order.split(","):
                part = part.strip()
                part_lower = part.lower()
                if " desc" in part_lower:
                    col = part_lower.replace(" desc", "").strip()
                    # Add ordering column to SELECT when it's not already there (needed for DISTINCT)
                    if col not in cols_to_select:
                        stmt = stmt.add_columns(sa.column(col))
                    stmt = stmt.order_by(sa.column(col).desc())
                else:
                    col = part_lower.replace(" asc", "").strip()
                    if col not in cols_to_select:
                        stmt = stmt.add_columns(sa.column(col))
                    stmt = stmt.order_by(sa.column(col).asc())
        elif self._order is True:
            # Attempt a DB-level pre-sort on the first label column; Python will re-sort later
            if self._label:
                stmt = stmt.order_by(sa.column(self._label[0]).asc())

        # Apply LEFT JOINs
        stmt = _apply_left_joins(stmt, self._table, self._left_join)

        result = db.execute(stmt)
        return [dict(row._mapping) for row in result]

    def find(self, db: Connection, ids: List[Any]) -> Union[List[IOption], bool]:
        """Retrieve formatted options for a specific set of values.

        Delegates to :meth:`exec` with ``refresh=False`` and *find=ids*, which
        bypasses ``search_only`` and ``always_refresh`` guards.

        Args:
            db:  SQLAlchemy :class:`~sqlalchemy.engine.Connection`.
            ids: List of option values to fetch.

        Returns:
            A list of matching :data:`IOption` dicts, or ``False`` when disabled.
        """
        return self.exec(db, refresh=False, find=ids)

    def search(self, db: Connection, term: str) -> Union[List[IOption], bool]:
        """Search for options whose rendered label starts with *term*.

        Delegates to :meth:`exec` with ``refresh=False`` and *search=term*.

        Args:
            db:   SQLAlchemy :class:`~sqlalchemy.engine.Connection`.
            term: Case-insensitive prefix to match against rendered labels.

        Returns:
            A list of matching :data:`IOption` dicts, or ``False`` when disabled.
        """
        return self.exec(db, refresh=False, search=term)
