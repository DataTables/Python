"""
datatables_server – Python/SQLAlchemy port of the DataTables Editor Node.js library.

This package provides server-side processing and CRUD support for
`DataTables <https://datatables.net/>`_ and
`Editor <https://editor.datatables.net/>`_, backed by SQLAlchemy Core instead
of Knex.js.

Quickstart::

    from datatables_server import Editor, Field, Validate, Format
    from sqlalchemy import create_engine

    engine = create_engine("postgresql://user:pass@localhost/mydb")

    with engine.connect() as conn:
        editor = (
            Editor(conn, "users")
            .fields(
                Field("first_name").validator(Validate.required()),
                Field("last_name").validator(Validate.required()),
                Field("email").validator(Validate.email()),
                Field("birth_date")
                    .get_formatter(Format.sql_date_to_format("%d/%m/%Y"))
                    .set_formatter(Format.format_to_sql_date("%d/%m/%Y")),
            )
        )
        editor.process(request_data)
        response = editor.data().to_dict()

Public API
----------
The following names are importable directly from the package root:

- :class:`Editor` – main CRUD / SSP processor
- :class:`Field` – column / field definition
- :class:`Mjoin` – many-to-many join helper
- :class:`Options` – select/radio/checkbox option source
- :class:`SearchBuilderOptions` – SearchBuilder option source
- :class:`SearchPaneOptions` – SearchPanes option source
- :class:`Upload` – file upload handler
- :class:`Format` – formatter factories (``Format.sql_date_to_format``, …)
- :class:`Validate` – validator factories (``Validate.required``, …)
- :class:`Column` – read-only column definition

Type aliases from :mod:`datatables_server.types`:

- :class:`DtError`
- :class:`DtOrder`
- :class:`DtColumnControl`
- :class:`DtColumn`
- :class:`DtRequest`
- :class:`DtResponse`
- :class:`LeftJoin`
- :class:`SspResult`
"""

from __future__ import annotations

from .column import Column
from .columncontrol import column_control_ssp
from .datatable import DataTable
from .editor import Editor, parse_form_data
from .helpers import unpadded_format
from .field import Field, SetType
from .formatters import Format, Formatter
from .mjoin import Mjoin
from .nested_data import NestedData
from .options import Options
from .search_builder_options import SearchBuilderOptions
from .search_pane_options import SearchPaneOptions
from .types import (
    DtColumn,
    DtColumnControl,
    DtError,
    DtOrder,
    DtRequest,
    DtResponse,
    LeftJoin,
    SspResult,
)
from .upload import DbOpts, Upload
from .validation_host import ValidationHost
from .validation_options import DependsOnFunc, ValidationOptions
from .validators import Validate

__all__ = [
    # Core classes
    "Editor",
    "parse_form_data",
    "unpadded_format",
    "DataTable",
    "Field",
    "SetType",
    "Mjoin",
    "Options",
    "SearchBuilderOptions",
    "SearchPaneOptions",
    "Upload",
    "DbOpts",
    "Column",
    "column_control_ssp",
    # Formatters / validators
    "Format",
    "Formatter",
    "Validate",
    # Validation infrastructure
    "ValidationOptions",
    "ValidationHost",
    "DependsOnFunc",
    # Base classes
    "NestedData",
    # Type aliases from types.py
    "DtError",
    "DtOrder",
    "DtColumnControl",
    "DtColumn",
    "DtRequest",
    "DtResponse",
    "LeftJoin",
    "SspResult",
]
