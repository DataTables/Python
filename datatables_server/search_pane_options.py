"""
Provides the :class:`SearchPaneOptions` class for configuring DataTables
SearchPanes options, and the :func:`construct_search_builder_query` helper that
translates SearchBuilder criteria into SQLAlchemy WHERE clauses.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Union

import sqlalchemy as sa
from sqlalchemy.engine import Connection
from sqlalchemy.sql.elements import ClauseElement

from .types import LeftJoin

# ---------------------------------------------------------------------------
# Public type aliases
# ---------------------------------------------------------------------------

IOption = Dict[str, Any]
"""A single option dict with at least ``'label'`` and ``'value'`` keys."""

IRenderer = Callable[[str], str]
"""A callable that receives a raw label string and returns a display string."""


# ---------------------------------------------------------------------------
# Module-level helpers
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
    ``LEFT JOIN … ON …`` clause is assembled from ``field1``, ``operator``,
    and ``field2``.

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
# SearchPaneOptions class
# ---------------------------------------------------------------------------


class SearchPaneOptions:
    """Configure options for DataTables SearchPanes fields.

    This is a port of the TypeScript ``SearchPaneOptions`` class from
    ``searchPaneOptions.ts``.  Instances are built with the chainable API
    and executed via :meth:`exec`.

    The class fetches ``DISTINCT (label, value)`` pairs from the configured
    table, optionally applies a total-count sub-query, LEFT JOINs, cascade
    filtering from active SearchPane selections, and returns the results with
    ``count`` and ``total`` values attached for each option.

    Typical usage::

        spo = (
            SearchPaneOptions()
            .table("statuses")
            .value("id")
            .label(["name"])
        )
        options = spo.exec(field, editor, http, fields_in, left_join_in)
    """

    def __init__(self) -> None:
        """Initialise a :class:`SearchPaneOptions` instance with default state."""
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

    def label(self, label: Optional[List[str]] = None) -> Union["SearchPaneOptions", List[str]]:
        """Get or set the label column(s).

        Args:
            label: List of database column names to use as the option label.
                   Omit to read the current value.

        Returns:
            The current label list when called with no argument; ``self``
            otherwise.
        """
        if label is None:
            return self._label
        self._label = list(label) if isinstance(label, list) else [label]
        return self

    def order(self, order: Optional[str] = None) -> Union["SearchPaneOptions", str]:
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

    def render(self, fn: Optional[IRenderer] = None) -> Union["SearchPaneOptions", Optional[IRenderer]]:
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

    def table(self, table: Optional[str] = None) -> Union["SearchPaneOptions", str]:
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

    def value(self, value: Optional[str] = None) -> Union["SearchPaneOptions", str]:
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

    def where(self, where: Any = None) -> Union["SearchPaneOptions", Any]:
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
    ) -> "SearchPaneOptions":
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
    ) -> List[Dict[str, Any]]:
        """Execute and return SearchPane options with per-option counts.

        Performs up to two database queries:

        1. **Options query** — ``SELECT DISTINCT label, value [, total] FROM table``
           with optional WHERE, LEFT JOINs, GROUP BY, and ORDER BY.  When
           ``viewTotal`` is requested, ``COUNT(*)`` is also selected so the
           un-filtered total can be included in the output.

        2. **Count sub-query** — when ``viewCount`` or ``cascade`` is enabled, a
           second query fetches ``DISTINCT value, COUNT(*)`` filtered by the
           currently active SearchPane selections from *http*, giving the count
           of rows that match the combined filter.

        Resolution order for each setting (first non-empty wins):

        * **value**: ``self._value`` → ``field.db_field()``
        * **label**: ``self._label`` → resolved *value*
        * **table**: ``self._table`` → ``editor.read_table()[0]`` →
          ``editor.table()[0]``
        * **joins**: instance joins merged with *left_join_in* (no duplicates)

        Args:
            field:        The :class:`~datatables_server.field.Field` instance
                          being configured.
            editor:       The :class:`~datatables_server.editor.Editor` instance
                          (provides ``db()``, ``table()``, ``read_table()``).
            http:         The raw HTTP request data dict.  Relevant keys:

                          * ``searchPanes_options`` — dict with boolean strings
                            ``viewCount``, ``viewTotal``, ``cascade``.
                          * ``searchPanes`` — dict mapping field name → list of
                            selected values.
                          * ``searchPanesLast`` — field name whose pane was most
                            recently changed (for cascade).
                          * ``searchPanes_null`` — dict mapping field name →
                            per-index null flags.
            fields_in:    All :class:`~datatables_server.field.Field` instances
                          for the current editor.
            left_join_in: Editor-level LEFT JOIN descriptors to merge with
                          instance-level joins.

        Returns:
            A list of dicts, each containing ``'label'``, ``'value'``,
            ``'count'``, and ``'total'`` keys.
        """
        db: Connection = editor.db()

        # ---- Read SearchPane display options from the HTTP request -------
        sp_options: Dict[str, Any] = http.get("searchPanes_options", {}) if isinstance(http, dict) else {}
        view_count: bool = sp_options.get("viewCount", "true") == "true"
        view_total: bool = sp_options.get("viewTotal", "false") == "true"
        cascade: bool = sp_options.get("cascade", "false") == "true"

        # ---- Resolve value column ----------------------------------------
        value_col: str = self._value if self._value else (field.db_field() if hasattr(field, "db_field") else "")

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

        # ---- Resolve label column(s) -------------------------------------
        label_src: Union[str, List[str]] = self._label if self._label else value_col
        first_label: str = label_src[0] if isinstance(label_src, list) else label_src

        # ---- Merge LEFT JOINs (instance joins first, then editor joins) --
        join: List[LeftJoin] = list(self._left_join)
        if left_join_in:
            existing_tables = {lj.table for lj in join}
            for lj in left_join_in:
                if lj.table not in existing_tables:
                    join.append(lj)
                    existing_tables.add(lj.table)

        # ---- Default renderer (identity) ---------------------------------
        formatter: IRenderer = self._renderer if self._renderer else (lambda d: d)

        # ---- Options query -----------------------------------------------
        # Selects label, value, and optionally a total count
        cols = [
            sa.column(first_label).label("label"),
            sa.column(value_col).label("value"),
        ]
        if view_total:
            cols.append(sa.func.count().label("total"))

        stmt: sa.Select = sa.select(*cols).select_from(sa.text(table)).distinct().group_by(sa.column(value_col))

        # Apply WHERE condition
        if self._where is not None:
            if callable(self._where):
                stmt = self._where(stmt)
            elif isinstance(self._where, dict):
                for k, v in self._where.items():
                    stmt = stmt.where(sa.column(k) == v)

        # Apply ORDER BY; also select any ordering columns not already present
        # (required for SELECT DISTINCT compatibility)
        if self._order:
            existing_col_names = {first_label, value_col}
            for part in self._order.split(","):
                part = part.strip()
                fie = part.lower().replace(" asc", "").replace(" desc", "").strip()
                if fie not in existing_col_names:
                    stmt = stmt.add_columns(sa.column(fie))
                    existing_col_names.add(fie)
            # Apply actual ordering
            for part in self._order.split(","):
                part = part.strip()
                part_lower = part.lower()
                if " desc" in part_lower:
                    col_name = part_lower.replace(" desc", "").strip()
                    stmt = stmt.order_by(sa.column(col_name).desc())
                else:
                    col_name = part_lower.replace(" asc", "").strip()
                    stmt = stmt.order_by(sa.column(col_name).asc())

        stmt = _apply_left_joins(stmt, join, table)

        rows = [dict(row._mapping) for row in db.execute(stmt)]

        # ---- Prune stale SearchPane selections ---------------------------
        # Remove selected values that no longer exist in the DB result set
        search_panes: Dict[str, Any] = (
            http.get("searchPanes", {}) if isinstance(http, dict) else getattr(http, "search_panes", None) or {}
        )
        field_name: str = field.name() if hasattr(field, "name") else ""
        if field_name and field_name in search_panes:
            existing_values = {r["value"] for r in rows}
            selected = search_panes[field_name]
            for i in range(len(selected) - 1, -1, -1):
                if selected[i] not in existing_values:
                    selected.pop(i)

        # ---- Count sub-query (cascade / viewCount) -----------------------
        entries: Optional[Dict[Any, Any]] = None
        if view_count or cascade:
            count_stmt: sa.Select = (
                sa.select(
                    sa.column(value_col).label("value"),
                )
                .select_from(sa.text(table))
                .distinct()
                .group_by(sa.column(value_col))
            )

            apply_get = True
            if hasattr(field, "apply") and hasattr(field, "get_value"):
                apply_get = field.apply("get") and not field.get_value()

            if apply_get:
                if view_count:
                    count_stmt = count_stmt.add_columns(sa.func.count().label("count"))
                else:
                    # We only need existence, not the cardinality
                    count_stmt = count_stmt.add_columns(sa.literal(1).label("count"))

            count_stmt = _apply_left_joins(count_stmt, join, table)

            # Build WHERE clauses from active SearchPane selections
            search_panes_null: Dict[str, Any] = (
                http.get("searchPanes_null", {})
                if isinstance(http, dict)
                else getattr(http, "search_panes_null", None) or {}
            )
            search_panes_last: Optional[str] = (
                http.get("searchPanesLast") if isinstance(http, dict) else getattr(http, "search_panes_last", None)
            )

            for fie in fields_in if fields_in else []:
                fie_name: str = fie.name() if hasattr(fie, "name") else str(fie)
                add = False

                if search_panes_last and field_name == search_panes_last:
                    # Cascade: this pane's count excludes its own selection but
                    # includes all other panes' selections
                    if fie_name in search_panes and fie_name != search_panes_last:
                        add = True
                elif fie_name in search_panes:
                    add = True

                if add:
                    fie_selections: List[Any] = search_panes[fie_name]
                    fie_null_flags: Dict[int, Any] = search_panes_null.get(fie_name, {})

                    # Build OR conditions for each selected value of this pane
                    or_clauses: List[ClauseElement] = []
                    for i, sel_val in enumerate(fie_selections):
                        null_flag = fie_null_flags.get(i) or fie_null_flags.get(str(i))
                        if null_flag and str(null_flag).lower() != "false":
                            or_clauses.append(sa.column(fie_name).is_(None))
                        else:
                            or_clauses.append(sa.column(fie_name) == sel_val)

                    if or_clauses:
                        count_stmt = count_stmt.where(sa.or_(*or_clauses))

            count_rows = [dict(row._mapping) for row in db.execute(count_stmt)]
            entries = {r["value"]: r for r in count_rows}

        # ---- Assemble output ---------------------------------------------
        out: List[Dict[str, Any]] = []
        for row in rows:
            row_value = row["value"]
            row_total: Optional[int] = row.get("total")
            row_count: Optional[int] = row_total  # default: count == total

            if entries is not None:
                entry = entries.get(row_value)
                row_count = entry["count"] if entry and "count" in entry else 0
                # When viewTotal is disabled, total must equal count
                if row_total is None:
                    row_total = row_count

            out.append(
                {
                    "label": formatter(row["label"]),
                    "total": row_total,
                    "value": row_value,
                    "count": row_count,
                }
            )

        # ---- Sort in Python-space when no SQL ORDER BY was specified -----
        if not self._order:

            def _sort_key(opt: Dict[str, Any]) -> tuple:
                lbl = opt["label"]
                if _is_numeric(lbl):
                    return (0, float(lbl), "")
                return (1, 0.0, str(lbl) if lbl is not None else "")

            out.sort(key=_sort_key)

        return out


# ---------------------------------------------------------------------------
# construct_search_builder_query
# ---------------------------------------------------------------------------


def _build_criteria_clause(crit: Dict[str, Any]) -> Optional[ClauseElement]:
    """Build a single SQLAlchemy clause element from a SearchBuilder criterion.

    Handles all condition types supported by the DataTables SearchBuilder
    plug-in.  For ``'null'`` / ``'!null'`` conditions the ``crit['type']``
    field is inspected to decide whether an empty-string check should be
    included alongside the ``IS NULL`` / ``IS NOT NULL`` test.

    Args:
        crit: A SearchBuilder criterion dict.  Expected keys:

              * ``origData`` — the database column name.
              * ``condition`` — the operator string (``'='``, ``'!='``,
                ``'contains'``, etc.).
              * ``value1`` — primary comparison value (may be absent for
                ``null`` / ``!null``).
              * ``value2`` — secondary value used by ``between`` / ``!between``.
              * ``type`` — field type string (used to suppress empty-string
                checks for date-like types).

    Returns:
        A SQLAlchemy :class:`~sqlalchemy.sql.elements.ClauseElement`, or
        ``None`` if the criterion is incomplete / unsupported.
    """
    condition: str = crit.get("condition", "")
    field_col: str = crit.get("origData", "")
    val1: Any = crit.get("value1")
    val2: Any = crit.get("value2")
    crit_type: str = crit.get("type", "")

    col = sa.column(field_col)

    # Guard: skip criteria that are missing required values
    if condition not in ("null", "!null") and (
        val1 is None or val1 == "" or (isinstance(val1, (list, str)) and len(val1) == 0)
    ):
        return None
    if condition in ("between", "!between") and (
        val2 is None or val2 == "" or (isinstance(val2, (list, str)) and len(val2) == 0)
    ):
        return None

    def _is_date_type(t: str) -> bool:
        return any(dt in t for dt in ("date", "moment", "luxon"))

    is_date = _is_date_type(crit_type)

    if condition == "=":
        return col == val1

    elif condition == "!=":
        return col != val1

    elif condition == "contains":
        return col.like(f"%{val1}%")

    elif condition == "!contains":
        return col.notlike(f"%{val1}%")

    elif condition == "starts":
        return col.like(f"{val1}%")

    elif condition == "!starts":
        return col.notlike(f"{val1}%")

    elif condition == "ends":
        return col.like(f"%{val1}")

    elif condition == "!ends":
        return col.notlike(f"%{val1}")

    elif condition == "<":
        return col < val1

    elif condition == "<=":
        return col <= val1

    elif condition == ">=":
        return col >= val1

    elif condition == ">":
        return col > val1

    elif condition == "between":
        lo = float(val1) if _is_numeric(val1) else val1
        hi = float(val2) if _is_numeric(val2) else val2
        return col.between(lo, hi)

    elif condition == "!between":
        lo = float(val1) if _is_numeric(val1) else val1
        hi = float(val2) if _is_numeric(val2) else val2
        return ~col.between(lo, hi)

    elif condition == "null":
        if is_date:
            return col.is_(None)
        return sa.or_(col.is_(None), col == "")

    elif condition == "!null":
        if is_date:
            return col.isnot(None)
        return sa.and_(col.isnot(None), col != "")

    return None


def _build_group_clause(sb_data: Dict[str, Any]) -> Optional[ClauseElement]:
    """Recursively build a SQLAlchemy clause from a SearchBuilder group node.

    A group node has a ``criteria`` list (which may contain further groups or
    leaf criteria) and a ``logic`` field (``'AND'`` or ``'OR'``).

    Args:
        sb_data: A SearchBuilder group dict with ``'logic'`` and ``'criteria'``
                 keys.

    Returns:
        A combined SQLAlchemy :class:`~sqlalchemy.sql.elements.ClauseElement`
        for the entire group, or ``None`` if no valid criteria were found.
    """
    logic: str = sb_data.get("logic", "AND")
    clauses: List[ClauseElement] = []

    for crit in sb_data.get("criteria", []):
        clause: Optional[ClauseElement] = None

        if "criteria" in crit:
            # Nested group — recurse
            clause = _build_group_clause(crit)
        elif "condition" in crit and ("value1" in crit or crit.get("condition") in ("null", "!null")):
            clause = _build_criteria_clause(crit)

        if clause is not None:
            clauses.append(clause)

    if not clauses:
        return None

    if logic == "OR":
        return sa.or_(*clauses)
    return sa.and_(*clauses)


def construct_search_builder_query(stmt: sa.Select, sb_data: Any) -> sa.Select:
    """Construct SQLAlchemy WHERE conditions from SearchBuilder criteria data.

    Recursively processes SearchBuilder criteria and adds a single combined
    WHERE condition to the provided SQLAlchemy select statement.  The function
    is a faithful port of the TypeScript ``constructSearchBuilderQuery`` in
    ``searchPaneOptions.ts``, translating Knex ``where`` / ``orWhere`` /
    ``whereNull`` / ``whereBetween`` calls into their SQLAlchemy Core
    equivalents.

    Supported condition strings
    ---------------------------
    * ``'='``       — exact equality
    * ``'!='``      — inequality (``!=``)
    * ``'contains'`` — SQL ``LIKE '%val%'``
    * ``'!contains'`` — SQL ``NOT LIKE '%val%'``
    * ``'starts'``  — SQL ``LIKE 'val%'``
    * ``'!starts'`` — SQL ``NOT LIKE 'val%'``
    * ``'ends'``    — SQL ``LIKE '%val'``
    * ``'!ends'``   — SQL ``NOT LIKE '%val'``
    * ``'<'``, ``'<='``, ``'>='``, ``'>'`` — numeric comparisons
    * ``'between'`` — SQL ``BETWEEN val1 AND val2`` (coerces to float when
      numeric)
    * ``'!between'`` — negated BETWEEN
    * ``'null'``    — ``IS NULL`` (and ``= ''`` unless a date type)
    * ``'!null'``   — ``IS NOT NULL`` (and ``!= ''`` unless a date type)

    Nested groups are supported: a criterion object that has a ``criteria``
    key is treated as a sub-group and processed recursively, with its own
    ``logic`` (``'AND'`` / ``'OR'``) combining its children.

    Args:
        stmt:    SQLAlchemy :class:`~sqlalchemy.sql.selectable.Select` statement
                 to add conditions to.
        sb_data: SearchBuilder data dict sent by the client.  Expected shape::

                     {
                         "logic": "AND" | "OR",
                         "criteria": [
                             {
                                 "condition": "=",
                                 "origData": "column_name",
                                 "value1": "some value",
                                 "type": "string"
                             },
                             {
                                 "logic": "OR",
                                 "criteria": [ … ]   # nested group
                             },
                             …
                         ]
                     }

    Returns:
        The modified :class:`~sqlalchemy.sql.selectable.Select` statement with
        an added ``WHERE`` clause.  If *sb_data* contains no valid criteria the
        original statement is returned unchanged.
    """
    if not sb_data:
        return stmt

    top_clause = _build_group_clause(sb_data)
    if top_clause is not None:
        stmt = stmt.where(top_clause)

    return stmt
