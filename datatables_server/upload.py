"""
File upload handling for DataTables Editor.

An :class:`Upload` instance is attached to a :class:`~datatables_server.Field`
via :meth:`~datatables_server.Field.upload`.  When Editor detects a file upload
for that field this instance controls how the file is stored on disk and
recorded in the database.
"""

from __future__ import annotations

import os
import shutil
from enum import Enum, auto
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union, TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.engine import Connection

if TYPE_CHECKING:
    from .editor import Editor
    from .field import Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class DbOpts(Enum):
    """Database field option types for :class:`Upload`.

    These constants are used as values in the ``fields`` dict passed to
    :meth:`Upload.db` to control what information about an uploaded file is
    stored in each database column.

    Attributes:
        CONTENT:      Store the raw file content (binary blob).
        CONTENT_TYPE: Store the MIME type (e.g. ``'image/jpeg'``).
        EXTN:         Store the file extension without the leading dot.
        NAME:         Store the file name without its extension.
        FILE_NAME:    Store the full file name (name + extension).
        FILE_SIZE:    Store the file size in bytes.
        MIME_TYPE:    Alias for ``CONTENT_TYPE``.
        READ_ONLY:    Do not write this column during an upload insert.
        SYSTEM_PATH:  Store the resolved system path to the saved file.
    """

    CONTENT = auto()
    CONTENT_TYPE = auto()
    EXTN = auto()
    NAME = auto()
    FILE_NAME = auto()
    FILE_SIZE = auto()
    MIME_TYPE = auto()
    READ_ONLY = auto()
    SYSTEM_PATH = auto()


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

#: Signature: ``(params, new_id) -> id``
DbUpdate = Callable[[Dict[str, Any], Optional[Union[str, bool]]], str]

#: Signature: ``(file_info, id, db_update) -> id``
UploadAction = Callable[[Dict[str, Any], str, DbUpdate], str]

#: Signature: ``(params) -> None``
DbFormat = Callable[[Dict[str, Any]], None]

#: Signature: ``(file_info, db) -> True | error_string``
DbValidate = Callable[[Dict[str, Any], Connection], Union[str, bool]]

#: Signature: ``(rows, db) -> bool`` — return True to delete the orphaned rows
DbCleanCallback = Callable[[List[Dict[str, Any]], Connection], bool]

# Re-export IFile as a dict type for compatibility with validators.
IFile = Dict[str, Any]


# ---------------------------------------------------------------------------
# Upload class
# ---------------------------------------------------------------------------


class Upload:
    """Handle file uploads for Editor fields.

    An :class:`Upload` instance is attached to a :class:`~datatables_server.Field`
    using :meth:`~datatables_server.Field.upload`.  When Editor detects a file
    upload for that field, this instance controls how the file is stored on disk
    and recorded in the database.

    Configuration is driven primarily through two methods:

    * :meth:`db` — describes how information about the uploaded file should be
      stored in the database.
    * :meth:`action` — describes where the file should be stored on disk, or
      provides a custom upload handler callable.

    Both methods are optional; a database-only or filesystem-only upload is
    supported.  Using both is the most common pattern.

    Example::

        from datatables_server import Upload, Field
        from datatables_server.upload import DbOpts

        field = Field('image_id').upload(
            Upload('/var/www/uploads/{name}.{extn}')
            .db(
                'dt_files',
                'id',
                {
                    'filename': DbOpts.FILE_NAME,
                    'filesize': DbOpts.FILE_SIZE,
                    'web_path': DbOpts.SYSTEM_PATH,
                    'mime_type': DbOpts.MIME_TYPE,
                },
            )
            .validator(Validate.file_extensions(['jpg', 'png'], 'Only images allowed'))
        )
    """

    # Legacy / convenience class-attribute aliases.
    Db = DbOpts
    DbOpts = DbOpts

    def __init__(self, action: Union[str, UploadAction] = None) -> None:
        """Create an Upload instance.

        Args:
            action: Either a path string (with ``{name}``, ``{extn}``, and
                    ``{id}`` tokens) or a callable that handles the upload.
                    Can also be set later via :meth:`action`.
        """
        self._action: Optional[Union[str, UploadAction]] = None
        self._db_clean_callback: Optional[DbCleanCallback] = None
        self._db_clean_table_field: Optional[Union[str, bool]] = None
        self._db_format: Optional[DbFormat] = None
        self._db_table: str = ""
        self._db_pkey: str = ""
        self._db_fields: Dict[str, Any] = {}
        self._error: Optional[str] = None
        self._validators: List[DbValidate] = []
        self._where: List[Any] = []

        if action is not None:
            self.action(action)

    # ------------------------------------------------------------------
    # Public configuration API
    # ------------------------------------------------------------------

    def action(self, action: Union[str, UploadAction]) -> "Upload":
        """Set the upload action — either a destination path or a custom handler.

        When a **string** is given it is treated as the destination path for the
        uploaded file.  Three tokens are substituted at upload time:

        * ``{name}``  — file name without extension.
        * ``{extn}``  — file extension (without the dot).
        * ``{id}``    — database primary key of the newly inserted row (only
                         available when :meth:`db` is also configured).

        When a **callable** is given it receives ``(file_info, id, db_update)``
        and is responsible for moving / processing the file.  *db_update* is a
        helper function that can insert or update a database row.

        Args:
            action: Path string with optional ``{name}``/``{extn}``/``{id}``
                    tokens, or a callable ``(file_info, id, db_update) -> id``.

        Returns:
            ``self`` for chaining.
        """
        self._action = action
        return self

    def db(
        self,
        table: str,
        pkey: str,
        fields: Dict[str, Any],
        format: DbFormat = None,
    ) -> "Upload":
        """Configure database recording for uploaded files.

        When configured, each file upload causes a row to be inserted into
        *table*.  The values written to each column are controlled by the
        *fields* mapping — values may be :class:`DbOpts` constants, static
        scalar values, or callables ``(db, file_info) -> value``.

        Args:
            table:  Database table name to insert file information into.
            pkey:   Primary key column name.  The inserted row's PK is returned
                    as the upload ID.
            fields: Mapping of column names to :class:`DbOpts` constants,
                    static values, or callables.
            format: Optional post-processing function called on each row
                    returned by :meth:`data`.  Mutates the row dict in-place.

        Returns:
            ``self`` for chaining.
        """
        self._db_table = table
        self._db_pkey = pkey
        self._db_fields = fields
        self._db_format = format
        return self

    def db_clean(
        self,
        table_field: Union[str, DbCleanCallback, bool],
        callback: DbCleanCallback = None,
    ) -> "Upload":
        """Configure cleanup of orphaned file records.

        Orphaned records are rows in the upload table that are no longer
        referenced by any row in the Editor's main table.

        Args:
            table_field: One of:

                * A ``'table.field'`` string — the column in the referencing
                  table that holds the upload FK.  Records in the upload table
                  not referenced here are considered orphaned.
                * A :class:`DbCleanCallback` callable — called directly with
                  ``(rows, db)``; the database querying logic is left entirely
                  to this function.
                * ``False`` — skip database actions entirely (useful when you
                  manage orphan detection yourself).

            callback: Function called with a list of orphaned row dicts and the
                      database connection.  Return ``True`` to also delete the
                      rows from the database; any other return value keeps them.

        Returns:
            ``self`` for chaining.
        """
        if callable(table_field):
            # Single-argument form: the callable IS the callback.
            self._db_clean_table_field = None
            self._db_clean_callback = table_field
        else:
            self._db_clean_table_field = table_field
            self._db_clean_callback = callback

        return self

    def validator(self, fn: DbValidate) -> "Upload":
        """Add a file validation function.

        Multiple validators can be added by calling this method multiple times.
        They are executed in order; the first failure aborts the upload and
        sets :meth:`error`.

        Args:
            fn: A callable ``(file_info, db) -> True | error_string``.

        Returns:
            ``self`` for chaining.
        """
        self._validators.append(fn)
        return self

    def where(self, fn: Any) -> "Upload":
        """Add a WHERE condition to the file data retrieval query in :meth:`data`.

        Args:
            fn: A SQLAlchemy WHERE clause expression or a callable that
                accepts a query and applies conditions.

        Returns:
            ``self`` for chaining.
        """
        self._where.append(fn)
        return self

    # ------------------------------------------------------------------
    # Internal API — called by Editor
    # ------------------------------------------------------------------

    def data(self, db: Connection, ids: List[str] = None) -> Optional[Dict[str, Any]]:
        """Retrieve file records from the database.

        Called internally by :class:`~datatables_server.Editor` to load the
        file data that is sent to the client alongside row data.

        Args:
            db:  Active SQLAlchemy :class:`~sqlalchemy.engine.Connection`.
            ids: Optional list of primary key values to restrict the query to.
                 When ``None`` all rows are returned.

        Returns:
            A dict mapping primary key values to row dicts, or ``None`` if no
            database table has been configured via :meth:`db`.
        """
        if not self._db_table:
            return None

        # Build the SELECT column list — skip binary CONTENT columns.
        select_cols = [sa.column(self._db_pkey)]
        for col_name, prop in self._db_fields.items():
            if prop != DbOpts.CONTENT:
                select_cols.append(sa.column(col_name))

        tbl = sa.table(self._db_table, *[sa.column(c.key) for c in select_cols])
        stmt = sa.select(*select_cols).select_from(tbl)

        if ids is not None:
            stmt = stmt.where(sa.column(self._db_pkey).in_(ids))

        for condition in self._where:
            if callable(condition):
                stmt = condition(stmt)
            else:
                stmt = stmt.where(condition)

        rows = db.execute(stmt).mappings().all()

        out: Dict[str, Any] = {}
        for row in rows:
            row_dict = dict(row)
            if self._db_format:
                self._db_format(row_dict)
            out[row_dict[self._db_pkey]] = row_dict

        return out

    def db_clean_exec(self, editor: "Editor", field: "Field") -> None:
        """Execute orphaned-file cleanup before processing the new upload.

        Called internally by :class:`~datatables_server.Editor`.  Cleanup runs
        *before* the new file is inserted so the fresh record is not
        immediately treated as orphaned.

        Args:
            editor: The owning :class:`~datatables_server.Editor` instance.
            field:  The :class:`~datatables_server.Field` the upload is
                    attached to.
        """
        tables = editor.table()
        self._db_clean(editor.db(), tables[0], field.db_field())

    def error(self) -> Optional[str]:
        """Return the last upload error message, or ``None`` if no error occurred.

        Returns:
            An error string, or ``None``.
        """
        return self._error

    def exec(self, editor: "Editor", upload: Dict[str, Any]) -> Optional[str]:
        """Execute the upload workflow and return the file ID.

        This is the main entry point called by
        :class:`~datatables_server.Editor` when a file upload is received.
        The steps are:

        1. Populate additional file metadata (size, extension, name).
        2. Run all registered :meth:`validator` functions.
        3. Insert a record into the database (if :meth:`db` is configured).
        4. Move / process the file (if :meth:`action` is configured).
        5. Return the file ID (database PK or file path).

        Args:
            editor: The owning :class:`~datatables_server.Editor` instance.
            upload: Dict containing an ``'upload'`` key whose value is a dict
                    with at minimum ``'file'`` (temp path) and ``'filename'``
                    (original name) keys.

        Returns:
            The file ID string on success, or ``None`` if an error occurred
            (inspect :meth:`error` for the message).
        """
        self._error = None
        file_info: Dict[str, Any] = upload.get("upload", upload)

        # Enrich the file_info dict with derived metadata.
        try:
            file_info["size"] = os.path.getsize(file_info["file"])
        except (OSError, KeyError):
            file_info["size"] = 0

        filename = file_info.get("filename", "")
        parts = filename.split(".")
        if len(parts) > 1:
            file_info["extn"] = parts[-1]
            file_info["name"] = ".".join(parts[:-1])
        else:
            file_info["extn"] = ""
            file_info["name"] = filename

        # Validate the uploaded file.
        for validate_fn in self._validators:
            result = validate_fn(file_info, editor.db())
            if isinstance(result, str):
                self._error = result
                return None

        # If a database table is configured, validate no SystemPath issues and insert.
        file_id: Any = None

        if self._db_table:
            for col_name, prop in self._db_fields.items():
                if not isinstance(self._action, str) and prop == DbOpts.SYSTEM_PATH:
                    self._error = (
                        "Cannot set path information in the database " "if a custom method is used to save the file."
                    )
                    return None

            file_id = self._db_exec(editor.db(), file_info)

        # Execute the action (move file to final location or call custom handler).
        result_id = self._action_exec(file_id, file_info, editor.db())
        return result_id

    def pkey(self) -> str:
        """Return the configured primary key column name.

        Returns:
            The primary key column name set via :meth:`db`.
        """
        return self._db_pkey

    def table(self) -> str:
        """Return the configured database table name.

        Returns:
            The table name set via :meth:`db`.
        """
        return self._db_table

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _action_exec(
        self,
        file_id: Any,
        file_info: Dict[str, Any],
        db: Connection,
    ) -> Optional[str]:
        """Execute the configured action to store the uploaded file.

        Args:
            file_id:   The database PK of the newly inserted upload row, or
                       ``None`` if no database table was configured.
            file_info: The enriched file metadata dict.
            db:        Active SQLAlchemy :class:`~sqlalchemy.engine.Connection`.

        Returns:
            The resolved file ID (database PK or final file path), or ``None``
            on error.
        """
        if callable(self._action):
            # Delegate entirely to the user-supplied function.
            def _db_update(params: Dict[str, Any], new_id: Optional[Union[str, bool]] = None) -> str:
                """Helper passed to custom action callables for DB updates.

                Args:
                    params: Column/value pairs to insert or update.
                    new_id: Pass a specific ID to use as the PK on insert, ``True``
                            to let the database generate one, or ``None``/omit to
                            update the existing row identified by *file_id*.

                Returns:
                    The ID of the affected row as a string.
                """
                if new_id is not None:
                    if new_id is not True:
                        params[self._db_pkey] = new_id
                    tbl = sa.table(self._db_table, *[sa.column(c) for c in params.keys()])
                    ins_stmt = sa.insert(tbl).values(**params)
                    if getattr(db.dialect, "insert_returning", False):
                        result = db.execute(ins_stmt.returning(sa.column(self._db_pkey)))
                        return str(result.scalar())
                    else:
                        result = db.execute(ins_stmt)
                        return str(result.lastrowid)
                else:
                    tbl = sa.table(self._db_table, *[sa.column(c) for c in params.keys()])
                    stmt = sa.update(tbl).values(**params).where(sa.column(self._db_pkey) == file_id)
                    db.execute(stmt)
                    return str(file_id)

            return self._action(file_info, str(file_id) if file_id is not None else "", _db_update)

        if self._action is None:
            # No action configured — return the database ID directly.
            return str(file_id) if file_id is not None else None

        # String action — substitute tokens and move the file.
        to = self._substitute(self._action, file_info.get("file", ""), str(file_id or ""))
        to = str(Path(to))

        try:
            os.makedirs(os.path.dirname(to), exist_ok=True)
            shutil.move(file_info["file"], to)
        except OSError:
            self._error = "An error occurred while moving the uploaded file."
            return None

        return str(file_id) if file_id is not None else to

    def _db_clean(self, db: Connection, editor_table: str, field_name: str) -> None:
        """Delete orphaned upload records and trigger the clean callback.

        An upload record is considered orphaned when its primary key is not
        referenced by any non-NULL value in the referencing table/column.

        Args:
            db:           Active SQLAlchemy :class:`~sqlalchemy.engine.Connection`.
            editor_table: The main table name of the owning Editor.
            field_name:   The column in the editor table (or a ``'table.field'``
                          string) that holds the upload FK.
        """
        callback = self._db_clean_callback

        # If explicitly set to False skip DB actions entirely.
        if self._db_clean_table_field is False:
            if callback:
                callback([], db)
            return

        if not self._db_table or not callback:
            return

        # Resolve the reference table and column.
        ref_field = self._db_clean_table_field if self._db_clean_table_field else field_name
        parts = str(ref_field).split(".")

        if len(parts) == 1:
            ref_table = editor_table
            ref_col = parts[0]
        elif len(parts) == 2:
            ref_table = parts[0]
            ref_col = parts[1]
        else:
            # schema.table.column — take the last two segments
            ref_table = parts[1]
            ref_col = parts[2]

        # Build SELECT for orphaned rows: rows in upload table whose PK is not
        # referenced by any non-NULL value in ref_table.ref_col.
        select_cols = [sa.column(self._db_pkey)]
        for col_name, prop in self._db_fields.items():
            if prop != DbOpts.CONTENT:
                select_cols.append(sa.column(col_name))

        upload_tbl = sa.table(self._db_table, *[sa.column(c.key) for c in select_cols])
        ref_subq = (
            sa.select(sa.column(ref_col))
            .select_from(sa.table(ref_table, sa.column(ref_col)))
            .where(sa.column(ref_col).isnot(None))
            .scalar_subquery()
        )

        stmt = sa.select(*select_cols).select_from(upload_tbl).where(sa.column(self._db_pkey).notin_(ref_subq))

        rows = [dict(r) for r in db.execute(stmt).mappings()]

        if not rows:
            return

        do_delete = callback(rows, db)

        if do_delete is True:
            pkey_vals = [r[self._db_pkey] for r in rows]
            del_tbl = sa.table(self._db_table, sa.column(self._db_pkey))
            del_stmt = sa.delete(del_tbl).where(sa.column(self._db_pkey).in_(pkey_vals))
            db.execute(del_stmt)

    def _db_exec(self, db: Connection, file_info: Dict[str, Any]) -> Any:
        """Insert a new row into the upload database table and return its PK.

        ``DbOpts.SYSTEM_PATH`` columns are first written with a placeholder
        (``'-'``) and then updated in a second statement once the final file
        path is known.

        Args:
            db:        Active SQLAlchemy :class:`~sqlalchemy.engine.Connection`.
            file_info: The enriched file metadata dict.

        Returns:
            The primary key value of the newly inserted row.
        """
        set_vals: Dict[str, Any] = {}
        path_fields: Dict[str, Any] = {}
        insert_id: Any = None

        for col_name, prop in self._db_fields.items():
            if prop == DbOpts.READ_ONLY:
                continue
            elif prop == DbOpts.CONTENT:
                with open(file_info["file"], "rb") as fh:
                    set_vals[col_name] = fh.read()
            elif prop in (DbOpts.CONTENT_TYPE, DbOpts.MIME_TYPE):
                set_vals[col_name] = file_info.get("mimetype", "")
            elif prop == DbOpts.EXTN:
                set_vals[col_name] = file_info.get("extn", "")
            elif prop == DbOpts.FILE_NAME:
                set_vals[col_name] = file_info.get("filename", "")
            elif prop == DbOpts.NAME:
                set_vals[col_name] = file_info.get("name", "")
            elif prop == DbOpts.FILE_SIZE:
                set_vals[col_name] = file_info.get("size", 0)
            elif prop == DbOpts.SYSTEM_PATH:
                # Written with a placeholder; updated after we know the final path.
                path_fields[col_name] = self._action
                set_vals[col_name] = "-"
            else:
                # Static value or callable
                val = prop(db, file_info) if callable(prop) else prop

                # If this column is the PK, capture the value for use as insertId.
                if col_name == self._db_pkey:
                    insert_id = val

                # If the value looks like a token pattern, defer it to a path update.
                if isinstance(val, str) and "{" in val and "}" in val:
                    path_fields[col_name] = val
                    set_vals[col_name] = "-"
                else:
                    set_vals[col_name] = val

        # Build and execute the INSERT.
        tbl = sa.table(self._db_table, *[sa.column(c) for c in set_vals.keys()])
        insert_stmt = sa.insert(tbl).values(**set_vals)

        if insert_id is not None:
            # PK value was supplied explicitly in the field config — just insert.
            db.execute(insert_stmt)
            row_id = insert_id
        elif getattr(db.dialect, "insert_returning", False):
            result = db.execute(insert_stmt.returning(sa.column(self._db_pkey)))
            row_id = result.scalar()
        else:
            result = db.execute(insert_stmt)
            row_id = result.lastrowid

        # Second pass: update SYSTEM_PATH / token columns now that we have the ID.
        if path_fields:
            update_vals: Dict[str, Any] = {}
            for col_name, pattern in path_fields.items():
                update_vals[col_name] = self._substitute(pattern, file_info.get("file", ""), str(row_id))

            upd_tbl = sa.table(self._db_table, *[sa.column(c) for c in update_vals.keys()])
            upd_stmt = sa.update(upd_tbl).values(**update_vals).where(sa.column(self._db_pkey) == row_id)
            db.execute(upd_stmt)

        return row_id

    def _substitute(self, pattern: str, upload_path: str, file_id: str) -> str:
        """Replace ``{name}``, ``{id}``, and ``{extn}`` tokens in *pattern*.

        Args:
            pattern:     The string containing tokens to substitute.
            upload_path: The temporary file path; used to derive name and extension.
            file_id:     The database primary key string to substitute for ``{id}``.

        Returns:
            The pattern with all tokens replaced.
        """
        # Derive name and extension from the upload path.
        file_name = os.path.basename(upload_path)
        file_parts = file_name.split(".")
        extn = file_parts.pop() if len(file_parts) > 1 else ""
        name_part = ".".join(file_parts)

        result = str(pattern)
        result = result.replace("{name}", name_part)
        result = result.replace("{id}", str(file_id))
        result = result.replace("{extn}", extn)
        return result
