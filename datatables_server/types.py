"""
Shared data-transfer types used across the datatables_server library.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Union

# ---------------------------------------------------------------------------
# Error / ordering / column primitives
# ---------------------------------------------------------------------------


@dataclass
class DtError:
    """Field error object returned in Editor responses."""

    name: str
    """Field name that triggered the error."""

    status: str
    """Human-readable error message."""

    id: Optional[str] = None
    """Row id that caused the error, if applicable."""

    def to_dict(self) -> Dict[str, Any]:
        """Convert to a JSON-serialisable dict, omitting None values.

        Returns:
            Dict with keys ``name``, ``status``, and optionally ``id``.
        """
        out: Dict[str, Any] = {"name": self.name, "status": self.status}
        if self.id is not None:
            out["id"] = self.id
        return out


@dataclass
class DtOrder:
    """DataTables server-side processing ordering descriptor."""

    dir: str
    """Sort direction – either ``'asc'`` or ``'desc'``."""

    column: int
    """Zero-based column index to sort by."""


@dataclass
class DtColumnControl:
    """Optional column-control metadata attached to a DataTables column."""

    list: Optional[List[str]] = None
    """List of column-control values."""

    search: Optional[Dict[str, Any]] = None
    """Column-control search configuration (``logic``, ``type``, ``value``, …)."""


@dataclass
class DtColumn:
    """DataTables server-side processing column descriptor."""

    data: str
    """The ``columns.data`` property sent by DataTables."""

    searchable: bool
    """Whether this column participates in the global search."""

    search: Dict[str, str] = field(default_factory=dict)
    """Per-column search object; at minimum contains a ``'value'`` key."""

    column_control: Optional[DtColumnControl] = None
    """Optional ColumnControl metadata for this column."""


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


@dataclass
class DtRequest:
    """DataTables / Editor HTTP request payload.

    Represents every field that DataTables (SSP) and Editor can send to the
    server in a single, unified object.
    """

    action: Optional[str] = None
    """Editor action being requested (``'create'``, ``'edit'``, or ``'remove'``)."""

    data: Optional[Dict[str, Dict[str, Any]]] = None
    """Editor row data keyed by row id."""

    draw: Optional[int] = None
    """DataTables SSP draw counter (echoed back in the response)."""

    field: Optional[str] = None
    """Dropdown / search field name (Editor options requests)."""

    ids: Optional[List[str]] = None
    """Specific row IDs to retrieve."""

    start: Optional[int] = None
    """DataTables SSP paging start index."""

    length: Optional[int] = None
    """DataTables SSP page length (``-1`` means no limit)."""

    order: Optional[List[DtOrder]] = None
    """DataTables SSP ordering descriptors."""

    columns: Optional[List[DtColumn]] = None
    """DataTables SSP column information."""

    search: Optional[Dict[str, str]] = None
    """DataTables SSP global search object (contains at least ``'value'``)."""

    search_builder: Optional[Any] = None
    """SearchBuilder query criteria sent by the client."""

    search_panes: Optional[Any] = None
    """SearchPanes selection data sent by the client."""

    search_panes_null: Optional[Any] = None
    """Indicates which SearchPanes panes are filtering for empty / null values."""

    upload_field: Optional[str] = None
    """Name of the field being used for a file upload."""

    values: Optional[List[Any]] = None
    """Dropdown label-lookup values."""


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------


@dataclass
class DtResponse:
    """Response object for DataTables SSP and Editor requests.

    All fields are optional; only those relevant to the specific operation need
    to be populated.  Call :meth:`to_dict` to serialise the response to the
    camelCase JSON format expected by DataTables / Editor on the client.
    """

    column_control: Optional[Dict[str, Any]] = None
    """ColumnControl options keyed by field name."""

    data: Optional[List[Dict[str, Any]]] = None
    """Array of row data objects."""

    cancelled: Optional[List[str]] = None
    """IDs of rows that were *not* acted upon (Editor)."""

    error: Optional[str] = None
    """General error string (DataTables and Editor)."""

    field_errors: Optional[List[DtError]] = None
    """Per-field validation errors (Editor)."""

    options: Optional[Dict[str, Any]] = None
    """``select`` / ``radio`` / ``checkbox`` option lists (Editor)."""

    files: Optional[Dict[str, Any]] = None
    """File information objects (Editor)."""

    draw: Optional[int] = None
    """DataTables SSP draw counter echo."""

    records_total: Optional[int] = None
    """Total number of records in the dataset before any filtering (SSP)."""

    records_filtered: Optional[int] = None
    """Number of records after applying the current filter (SSP)."""

    search_builder: Optional[Any] = None
    """SearchBuilder options / counts to return to the client."""

    search_panes: Optional[Any] = None
    """SearchPanes options / counts to return to the client."""

    upload: Optional[Dict[str, str]] = None
    """Upload result containing at minimum an ``'id'`` key (Editor)."""

    debug: Optional[List[Any]] = None
    """Debug information when :meth:`~datatables_server.Editor.debug` is enabled."""

    def to_dict(self) -> Dict[str, Any]:
        """Serialise the response to a camelCase dict compatible with DataTables.

        All ``None``-valued fields are omitted from the output.  Field errors are
        serialised via :meth:`DtError.to_dict`.

        Returns:
            A JSON-serialisable ``dict`` using the camelCase key names that the
            DataTables / Editor client libraries expect.
        """
        # Mapping: Python attribute name → camelCase JSON key
        _key_map: Dict[str, str] = {
            "column_control": "columnControl",
            "data": "data",
            "cancelled": "cancelled",
            "error": "error",
            "field_errors": "fieldErrors",
            "options": "options",
            "files": "files",
            "draw": "draw",
            "records_total": "recordsTotal",
            "records_filtered": "recordsFiltered",
            "search_builder": "searchBuilder",
            "search_panes": "searchPanes",
            "upload": "upload",
            "debug": "debug",
        }

        out: Dict[str, Any] = {}

        for attr, json_key in _key_map.items():
            value = getattr(self, attr)
            if value is None:
                continue

            # Serialise DtError instances nested inside field_errors
            if attr == "field_errors":
                out[json_key] = [e.to_dict() if isinstance(e, DtError) else e for e in value]
            else:
                out[json_key] = value

        return out


# ---------------------------------------------------------------------------
# Join / SSP helpers
# ---------------------------------------------------------------------------


@dataclass
class LeftJoin:
    """Configuration for a LEFT JOIN clause."""

    table: str
    """Name of the table to join onto."""

    field1: Optional[str] = None
    """Column from the parent table to use as the join key."""

    field2: Optional[str] = None
    """Column from the joined table to use as the join key."""

    operator: Optional[str] = None
    """Comparison operator (e.g. ``'='``, ``'<'``, etc.)."""

    fn: Optional[Callable] = None
    """Optional callable used to build a complex join condition."""


@dataclass
class SspResult:
    """Internal server-side processing count result."""

    draw: Optional[int] = None
    """Echoed draw counter."""

    records_filtered: Optional[int] = None
    """Record count after filtering."""

    records_total: Optional[int] = None
    """Total record count before filtering."""
