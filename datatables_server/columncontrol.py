"""
columncontrol.py – Server-side processing helpers for the DataTables ColumnControl extension.

This module provides :func:`column_control_ssp`, a function that inspects the
``columns[].columnControl`` property of a DataTables SSP request and applies the
appropriate WHERE clauses to a SQLAlchemy ``Select`` statement.

It is called automatically by :class:`~datatables_server.editor.Editor` during
server-side processing when ColumnControl data is present in the request, but can
also be used standalone if you are building a custom query pipeline.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

if TYPE_CHECKING:
    from .editor import Editor
    from .field import Field
    from .types import DtRequest


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def column_control_ssp(editor: "Editor", stmt: sa.Select, http: "DtRequest") -> sa.Select:
    """Apply ColumnControl search and list conditions to *stmt*.

    Iterates over every column in *http* that carries a ``columnControl`` payload
    and adds the appropriate WHERE clause to the SELECT statement.

    Supported control types:

    * **SearchList** (``columnControl.list``) – adds a ``WHERE column IN (…)``
      condition.
    * **Search input** (``columnControl.search``) – supports ``text``, ``num``
      and ``date`` logic modes, each with their own set of comparison operators
      (``equal``, ``notEqual``, ``contains``, ``notContains``, ``starts``,
      ``ends``, ``greater``, ``less``, ``greaterOrEqual``, ``lessOrEqual``,
      ``empty``, ``notEmpty``).

    Args:
        editor: The :class:`~datatables_server.editor.Editor` (or
            :class:`~datatables_server.datatable.DataTable`) instance whose
            ``field()`` method is used to resolve column names.
        stmt:   The SQLAlchemy ``Select`` statement to extend.
        http:   The parsed DataTables request containing column metadata.

    Returns:
        The (possibly modified) ``Select`` statement.
    """
    if not http.columns:
        return stmt

    for col in http.columns:
        if not col.column_control:
            continue

        try:
            field: "Field" = editor.field(col.data)
        except Exception:
            continue

        cc = col.column_control

        # --- SearchList (multi-value IN filter) ---
        if cc.list:
            stmt = stmt.where(sa.literal_column(field.db_field()).in_(cc.list))

        # --- Search input ---
        if cc.search:
            search = cc.search
            value: str = search.get("value", "")
            logic: str = search.get("logic", "")
            s_type: str = search.get("type", "text")
            mask: str | None = search.get("mask")

            if s_type == "num":
                stmt = _ssp_number(stmt, field, value, logic)
            elif s_type == "date":
                stmt = _ssp_date(stmt, field, value, logic, mask)
            else:
                stmt = _ssp_text(stmt, field, value, logic)

    return stmt


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _ssp_date(
    stmt: sa.Select,
    field: "Field",
    value: str,
    logic: str,
    mask: str | None,
) -> sa.Select:
    """Apply a ColumnControl **date** search condition to *stmt*.

    Only ``YYYY-MM-DD`` (date) and ``hh:mm:ss`` (time) masks are supported,
    matching the behaviour of the Node.js library.  For masked comparisons the
    appropriate SQL ``DATE()`` or ``TIME()`` function is wrapped around both the
    column and the bound parameter.

    Args:
        stmt:   SELECT statement to extend.
        field:  Field that the column maps to.
        value:  Search value string.
        logic:  Comparison operator name (``equal``, ``notEqual``, ``greater``,
                ``less``, ``empty``, ``notEmpty``).
        mask:   Optional format mask from the client (``YYYY-MM-DD`` or
                ``hh:mm:ss``).

    Returns:
        Modified SELECT statement.
    """
    db_field = field.db_field()

    if mask == "YYYY-MM-DD":
        col_expr: Any = sa.func.DATE(sa.literal_column(db_field))
        val_expr: Any = sa.func.DATE(sa.bindparam("_cc_date_val", value))
    elif mask == "hh:mm:ss":
        col_expr = sa.func.TIME(sa.literal_column(db_field))
        val_expr = sa.func.TIME(sa.bindparam("_cc_time_val", value))
    else:
        col_expr = sa.literal_column(db_field)
        val_expr = sa.literal(value)

    raw_col = sa.literal_column(db_field)

    if logic == "empty":
        return stmt.where(raw_col.is_(None))
    if logic == "notEmpty":
        return stmt.where(raw_col.isnot(None))
    if not value:
        return stmt
    if logic == "equal":
        return stmt.where(col_expr == val_expr)
    if logic == "notEqual":
        return stmt.where(col_expr != val_expr)
    if logic == "greater":
        return stmt.where(col_expr > val_expr)
    if logic == "less":
        return stmt.where(col_expr < val_expr)

    return stmt


def _ssp_number(stmt: sa.Select, field: "Field", value: str, logic: str) -> sa.Select:
    """Apply a ColumnControl **numeric** search condition to *stmt*.

    Args:
        stmt:   SELECT statement to extend.
        field:  Field that the column maps to.
        value:  Search value string.
        logic:  Comparison operator name (``equal``, ``notEqual``, ``greater``,
                ``greaterOrEqual``, ``less``, ``lessOrEqual``, ``empty``,
                ``notEmpty``).

    Returns:
        Modified SELECT statement.
    """
    col = sa.literal_column(field.db_field())

    if logic == "empty":
        return stmt.where(sa.or_(col.is_(None), col == ""))
    if logic == "notEmpty":
        return stmt.where(sa.and_(col.isnot(None), col != ""))
    if not value:
        return stmt
    if logic == "equal":
        return stmt.where(col == value)
    if logic == "notEqual":
        return stmt.where(col != value)
    if logic == "greater":
        return stmt.where(col > value)
    if logic == "greaterOrEqual":
        return stmt.where(col >= value)
    if logic == "less":
        return stmt.where(col < value)
    if logic == "lessOrEqual":
        return stmt.where(col <= value)

    return stmt


def _ssp_text(stmt: sa.Select, field: "Field", value: str, logic: str) -> sa.Select:
    """Apply a ColumnControl **text** search condition to *stmt*.

    Args:
        stmt:   SELECT statement to extend.
        field:  Field that the column maps to.
        value:  Search value string.
        logic:  Comparison operator name (``equal``, ``notEqual``,
                ``contains``, ``notContains``, ``starts``, ``ends``,
                ``empty``, ``notEmpty``).

    Returns:
        Modified SELECT statement.
    """
    col = sa.literal_column(field.db_field())

    if logic == "empty":
        return stmt.where(sa.or_(col.is_(None), col == ""))
    if logic == "notEmpty":
        return stmt.where(sa.and_(col.isnot(None), col != ""))
    if not value:
        return stmt
    if logic == "equal":
        return stmt.where(col == value)
    if logic == "notEqual":
        return stmt.where(col != value)
    if logic == "contains":
        return stmt.where(col.like(f"%{value}%"))
    if logic == "notContains":
        return stmt.where(col.notlike(f"%{value}%"))
    if logic == "starts":
        return stmt.where(col.like(f"{value}%"))
    if logic == "ends":
        return stmt.where(col.like(f"%{value}"))

    return stmt
