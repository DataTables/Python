"""
The :class:`SearchBuilderOptions` class configures how distinct value/label pairs
are retrieved from the database for use by the DataTables SearchBuilder plug-in.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Union

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from .types import LeftJoin

# ---------------------------------------------------------------------------
# Public type aliases (mirroring options.py)
# ---------------------------------------------------------------------------

IOption = Dict[str, Any]
"""A single option dict with at least ``'label'`` and ``'value'`` keys."""

IRenderer = Callable[[str], str]
"""A callable that receives a raw label string and returns a display string."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_numeric(value: Any) -> bool:
    """Return ``True`` if *value* can be interpreted as a finite number.

    Args:
        value: Any value to test.

    Returns:
        ``True`` when *value* is a finite number or a string representation
        of one, ``False`` otherwise.
    """
    try:
        f = float(value)
        return f == f and f not in (float("inf"), float("-inf"))
    except (TypeError, ValueError):
        return False


def _apply_left_joins(stmt: sa.Select, left_joins: List[LeftJoin], table: str) -> sa.Select:
    """Apply a list of :class:`~datatables_server.types.LeftJoin` descriptors to *stmt*.

    For each entry, if a callable ``fn`` is provided it receives the statement
    and must return the modified statement.  Otherwise, a raw SQL
    ``LEFT JOIN … ON …`` clause is assembled from ``table``, ``field1``,
    ``operator``, and ``field2``.

    Args:
        stmt:       The :class:`~sqlalchemy.sql.selectable.Select` to augment.
        left_joins: Ordered list of :class:`~datatables_server.types.LeftJoin`
                    descriptors.
        table:      Primary table name used as the left-hand side for plain joins.

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
# SearchBuilderOptions class
# ---------------------------------------------------------------------------


class SearchBuilderOptions:
    """Configure options retrieved for SearchBuilder fields.

    This is a port of the TypeScript ``SearchBuilderOptions`` class from
    ``searchBuilderOptions.ts``.  Instances are built with the chainable API
    and executed via :meth:`exec`.

    The class fetches ``DISTINCT (label, value)`` pairs from the configured
    table, optionally applies WHERE conditions and LEFT JOINs, and returns
    them sorted for presentation in the SearchBuilder UI.

    Typical usage::

        sbo = (
            SearchBuilderOptions()
            .table("countries")
            .value("id")
            .label(["name"])
        )
        options = sbo.exec(field, editor, http, fields_in, left_join_in)
    """

    def __init__(self) -> None:
        """Initialise a :class:`SearchBuilderOptions` instance with default state."""
        self._table: str = ""
        self._value: str = ""
        self._label: List[str] = []
        self._left_join: List[LeftJoin] = []
        self._renderer: Optional[IRenderer] = None
        self._where: Any = None
        self._order: str = ""

    # ------------------------------------------------------------------
    # Chainable configuration API
    # ------------------------------------------------------------------

    def label(self, label: Optional[List[str]] = None) -> Union["SearchBuilderOptions", List[str]]:
        """Get or set the label column(s).

        Args:
            label: List of database column names to use as the label.
                   Omit to read the current value.

        Returns:
            The current label list when called with no argument; ``self``
            otherwise.
        """
        if label is None:
            return self._label
        self._label = list(label) if isinstance(label, list) else [label]
        return self

    def order(self, order: Optional[str] = None) -> Union["SearchBuilderOptions", str]:
        """Get or set the ORDER BY clause.

        When not set (empty string) the results are sorted in Python-space
        by label value after the query executes.

        Args:
            order: SQL ``ORDER BY`` expression (e.g. ``'name asc'``).
                   Omit to read the current value.

        Returns:
            The current order expression when called with no argument; ``self``
            otherwise.
        """
        if order is None:
            return self._order
        self._order = order
        return self

    def render(self, fn: Optional[IRenderer] = None) -> Union["SearchBuilderOptions", Optional[IRenderer]]:
        """Get or set a label renderer function.

        The renderer receives the raw label string from the database and returns
        the display string.  When not set the raw value is used as-is.

        Args:
            fn: Renderer callable ``(str) -> str``.  Omit to read the current
                value.

        Returns:
            The current renderer when called with no argument; ``self``
            otherwise.
        """
        if fn is None:
            return self._renderer
        self._renderer = fn
        return self

    def table(self, table: Optional[str] = None) -> Union["SearchBuilderOptions", str]:
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

    def value(self, value: Optional[str] = None) -> Union["SearchBuilderOptions", str]:
        """Get or set the value column name.

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

    def where(self, where: Any = None) -> Union["SearchBuilderOptions", Any]:
        """Get or set a WHERE condition for the options query.

        Accepted forms:

        * A **callable** ``(stmt: Select) -> Select`` — receives the statement
          and must return a modified one.
        * A **dict** ``{column: value, …}`` — converted to equality conditions
          joined with ``AND``.

        Args:
            where: WHERE specification.  Omit to read the current value.

        Returns:
            The current condition when called with no argument; ``self``
            otherwise.
        """
        if where is None:
            return self._where
        self._where = where
        return self

    def left_join(
        self,
        table: str,
        field1_or_fn: Union[str, Callable],
        operator: Optional[str] = None,
        field2: Optional[str] = None,
    ) -> "SearchBuilderOptions":
        """Add a LEFT JOIN to the options query.

        Two call signatures are supported:

        * ``left_join(table, fn)`` — *field1_or_fn* is a callable that receives
          the current :class:`~sqlalchemy.sql.selectable.Select` and returns a
          modified one.
        * ``left_join(table, field1, operator, field2)`` — plain field join.

        Args:
            table:        Name of the table to join onto.
            field1_or_fn: Left-hand field name or a statement-mutating callable.
            operator:     Comparison operator (e.g. ``'='``).
            field2:       Right-hand field name.

        Returns:
            ``self`` for method chaining.
        """
        if self._left_join is None:
            self._left_join = []

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

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def exec(
        self,
        field: Any,
        editor: Any,
        http: Any,
        fields_in: Any,
        left_join_in: Any,
    ) -> List[IOption]:
        """Execute and return the SearchBuilder option list.

        Resolves the effective table, value column, label column(s), and join
        list from the instance configuration and the supplied *field* / *editor*
        context.  Runs a ``SELECT DISTINCT label, value … GROUP BY value`` query,
        then sorts the results if no SQL ``ORDER BY`` was specified.

        Resolution order for each setting (first non-empty wins):

        * **value**: ``self._value`` → ``field.db_field()`` → ``field.name()``
        * **label**: ``self._label`` → resolved *value*
        * **table**: ``self._table`` → ``editor.read_table()[0]`` →
          ``editor.table()[0]``
        * **joins**: ``self._left_join`` (merged with *left_join_in*, skipping
          tables already present)

        Args:
            field:        The :class:`~datatables_server.field.Field` instance
                          whose SearchBuilder options are being resolved.
            editor:       The :class:`~datatables_server.editor.Editor` instance
                          (provides ``db()``, ``table()``, ``read_table()``).
            http:         The raw HTTP request data dict (not used directly here,
                          passed for API compatibility).
            fields_in:    All field instances for the current editor (not used
                          directly here).
            left_join_in: Editor-level LEFT JOIN descriptors to merge with the
                          instance-level joins.

        Returns:
            A list of :data:`~datatables_server.types.IOption` dicts, each with
            ``'value'`` and ``'label'`` keys.
        """
        options = field.search_builder_options() if hasattr(field, "search_builder_options") else None
        if options is None:
            return []

        # ---- Resolve value column ----------------------------------------
        if not self._value:
            sbo_label = options.label() if hasattr(options, "label") else None
            if sbo_label:
                value_col = sbo_label[0]
            elif hasattr(field, "name"):
                value_col = field.name()
            else:
                value_col = ""
        else:
            value_col = self._value

        # ---- Resolve label column(s) -------------------------------------
        label_cols: Union[str, List[str]] = self._label if self._label else value_col

        # ---- Resolve table -----------------------------------------------
        if self._table:
            table = self._table
        else:
            read_table = editor.read_table() if hasattr(editor, "read_table") else []
            if read_table:
                table = read_table[0]
            else:
                editor_table = editor.table() if hasattr(editor, "table") else []
                table = editor_table[0] if editor_table else ""

        # ---- Merge LEFT JOINs --------------------------------------------
        join: List[LeftJoin] = list(self._left_join)
        if left_join_in and not join:
            # Only adopt editor-level joins when no instance-level joins exist
            join = list(left_join_in)

        # ---- Default renderer (identity) ---------------------------------
        formatter: IRenderer = self._renderer if self._renderer else (lambda s: s)

        # ---- Build the query ---------------------------------------------
        db: Connection = editor.db()

        # Use the first label column for the label alias; join extras with space via Python
        first_label = label_cols[0] if isinstance(label_cols, list) else label_cols

        stmt = (
            sa.select(
                sa.column(first_label).label("label"),
                sa.column(value_col).label("value"),
            )
            .select_from(sa.text(table))
            .distinct()
            .group_by(sa.column(value_col))
        )

        # Apply WHERE condition
        if self._where is not None:
            if callable(self._where):
                stmt = self._where(stmt)
            elif isinstance(self._where, dict):
                for k, v in self._where.items():
                    stmt = stmt.where(sa.column(k) == v)

        # Apply ORDER BY in SQL when specified
        if self._order:
            for part in self._order.split(","):
                part = part.strip()
                part_lower = part.lower()
                if " desc" in part_lower:
                    col = part_lower.replace(" desc", "").strip()
                    stmt = stmt.order_by(sa.column(col).desc())
                else:
                    col = part_lower.replace(" asc", "").strip()
                    stmt = stmt.order_by(sa.column(col).asc())

        # Apply LEFT JOINs
        stmt = _apply_left_joins(stmt, join, table)

        result = db.execute(stmt)
        rows = [dict(row._mapping) for row in result]

        out: List[IOption] = [{"value": row["value"], "label": formatter(row["label"])} for row in rows]

        # Sort in Python-space when no SQL ORDER BY was specified
        if not self._order:

            def _sort_key(opt: IOption) -> tuple:
                lbl = opt["label"]
                if _is_numeric(lbl):
                    return (0, float(lbl), "")
                return (1, 0.0, str(lbl) if lbl is not None else "")

            out.sort(key=_sort_key)

        return out
