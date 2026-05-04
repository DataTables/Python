"""
Validation factory methods for DataTables Editor fields.

All methods on :class:`Validate` return a validator function compatible with
:meth:`~datatables_server.Field.validator`. Each validator returns ``True`` for
valid data or an error string for invalid data.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Union, TYPE_CHECKING

import sqlalchemy as sa

from .validation_options import ValidationOptions
from .validation_host import ValidationHost

if TYPE_CHECKING:
    from .editor import Editor

# IFile is a plain dict of upload metadata — defined locally to avoid circular imports.
IFile = Dict[str, Any]

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

IValidator = Callable[[Any, dict, ValidationHost], Union[bool, str]]
"""A validator callable: ``(val, data, host) -> True | error_string``."""

IFileValidator = Callable[["IFile", Any], Union[bool, str]]
"""A file-upload validator callable: ``(file, db) -> True | error_string``.

The ``db`` argument is the active SQLAlchemy connection and may be ignored
for simple checks (extension, size) but is available for custom validators
that need to query the database.
"""

IMjoinValidator = Callable[["Editor", str, List[Any]], Union[bool, str]]
"""An Mjoin validator callable: ``(editor, action, data) -> True | error_string``."""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_URL_RE = re.compile(
    r"^(https?|ftp)://"  # scheme
    r"(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+(?:[A-Z]{2,6}\.?|[A-Z0-9-]{2,}\.?)|"  # domain
    r"localhost|"  # localhost
    r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})"  # IP
    r"(?::\d+)?"  # optional port
    r"(?:/?|[/?]\S+)$",
    re.IGNORECASE,
)

_EMAIL_RE = re.compile(
    r"^(([^<>()\[\]\.,;:\s@\"]+(\.[^<>()\[\]\.,;:\s@\"]+)*)|(\".+\"))"
    r"@(([^<>()[\]\.,;:\s@\"]+\.)+[^<>()[\]\.,;:\s@\"]{2,})$",
    re.IGNORECASE,
)


class Validate:
    """Validation factory methods for DataTables Editor fields.

    Each static method returns a validator function compatible with
    :meth:`~datatables_server.Field.validator`.  Validators return ``True``
    for valid input or an error string describing why the input is invalid.

    Usage example::

        from datatables_server import Field, Validate

        field = (
            Field('email')
            .validator(Validate.required())
            .validator(Validate.email())
        )

    Most methods accept an optional :class:`~datatables_server.ValidationOptions`
    instance to customise the error message and empty/optional behaviour.
    """

    # Convenience alias so callers can write ``Validate.Options(...)``
    Options = ValidationOptions

    # ------------------------------------------------------------------
    # Basic validators
    # ------------------------------------------------------------------

    @staticmethod
    def none(cfg: ValidationOptions = None) -> IValidator:
        """No validation — all submitted values are considered valid.

        Args:
            cfg: Optional :class:`~datatables_server.ValidationOptions`.
                 Ignored for this validator.

        Returns:
            A validator that always returns ``True``.
        """

        def _validator(val: Any, data: dict, host: ValidationHost) -> Union[bool, str]:
            return True

        return _validator

    @staticmethod
    def basic(cfg: ValidationOptions = None) -> IValidator:
        """Basic validation using only the :class:`~datatables_server.ValidationOptions` settings.

        The field passes validation if the common conditions (required, empty,
        optional) are all satisfied — the actual value is not inspected further.

        Args:
            cfg: Optional :class:`~datatables_server.ValidationOptions` to
                 control the ``required``/``empty``/``optional`` behaviour and
                 error message.

        Returns:
            A validator that passes based on ``ValidationOptions`` rules only.
        """
        opts = ValidationOptions.select(cfg)

        def _validator(val: Any, data: dict, host: ValidationHost) -> Union[bool, str]:
            common = Validate._common(val, opts, data, host)
            return opts.message() if common is False else True

        return _validator

    @staticmethod
    def required(cfg: ValidationOptions = None) -> IValidator:
        """Validate that a non-empty value was submitted.

        Both ``empty`` and ``optional`` are set to ``False`` so the field must
        be present in the submission and must not be an empty string.

        Args:
            cfg: Optional :class:`~datatables_server.ValidationOptions` to
                 override the default error message.

        Returns:
            A validator that requires a non-empty value.
        """
        opts = ValidationOptions.select(cfg)
        opts.empty(False)
        opts.optional(False)

        def _validator(val: Any, data: dict, host: ValidationHost) -> Union[bool, str]:
            common = Validate._common(val, opts, data, host)
            return opts.message() if common is False else True

        return _validator

    @staticmethod
    def not_empty(cfg: ValidationOptions = None) -> IValidator:
        """Validate that, if a value is submitted, it is not an empty string.

        The field may be absent from the submission (it is optional), but when
        present it must have a non-empty value.

        Args:
            cfg: Optional :class:`~datatables_server.ValidationOptions`.

        Returns:
            A validator that rejects empty strings but accepts absent fields.
        """
        opts = ValidationOptions.select(cfg)
        opts.empty(False)

        def _validator(val: Any, data: dict, host: ValidationHost) -> Union[bool, str]:
            common = Validate._common(val, opts, data, host)
            return opts.message() if common is False else True

        return _validator

    @staticmethod
    def boolean(cfg: ValidationOptions = None) -> IValidator:
        """Validate that the submitted value is a recognised boolean representation.

        Accepted truthy strings/values: ``True``, ``1``, ``'1'``, ``'true'``,
        ``'t'``, ``'on'``, ``'yes'``, ``'✓'``, ``'x'``.
        Accepted falsy strings/values: ``False``, ``0``, ``'0'``, ``'false'``,
        ``'f'``, ``'off'``, ``'no'``.

        Args:
            cfg: Optional :class:`~datatables_server.ValidationOptions`.

        Returns:
            A validator that accepts boolean-like values.
        """
        opts = ValidationOptions.select(cfg)

        _truthy = {True, 1, "1", "true", "t", "on", "yes", "✓", "x"}
        _falsy = {False, 0, "0", "false", "f", "off", "no"}

        def _validator(val: Any, data: dict, host: ValidationHost) -> Union[bool, str]:
            common = Validate._common(val, opts, data, host)
            if common is not None:
                return opts.message() if common is False else True

            check = val.lower() if isinstance(val, str) else val
            if check in _truthy or check in _falsy:
                return True

            return opts.message()

        return _validator

    # ------------------------------------------------------------------
    # Numeric validators
    # ------------------------------------------------------------------

    @staticmethod
    def numeric(decimal: str = ".", cfg: ValidationOptions = None) -> IValidator:
        """Validate that the submitted value is numeric.

        Args:
            decimal: The character used as the decimal separator in the input
                     (defaults to ``'.'``).  When set to e.g. ``','`` the value
                     is normalised before parsing.
            cfg:     Optional :class:`~datatables_server.ValidationOptions`.

        Returns:
            A validator that accepts numeric strings and numbers.
        """
        opts = ValidationOptions.select(cfg)

        def _validator(val: Any, data: dict, host: ValidationHost) -> Union[bool, str]:
            common = Validate._common(val, opts, data, host)
            if common is not None:
                return opts.message() if common is False else True

            if isinstance(val, (int, float)):
                return True

            s = str(val)
            if decimal != ".":
                s = s.replace(decimal, ".", 1)

            s = s.strip()
            if s == "":
                return opts.message()

            try:
                float(s)
                return True
            except ValueError:
                return opts.message()

        return _validator

    @staticmethod
    def min_num(min: float, decimal: str = ".", cfg: ValidationOptions = None) -> IValidator:
        """Validate that the value is numeric and >= *min*.

        Args:
            min:     The minimum allowed value (inclusive).
            decimal: Decimal separator character (defaults to ``'.'``).
            cfg:     Optional :class:`~datatables_server.ValidationOptions`.

        Returns:
            A validator that rejects values below *min*.
        """
        opts = ValidationOptions.select(cfg)
        _num_validator = Validate.numeric(decimal, opts)

        def _validator(val: Any, data: dict, host: ValidationHost) -> Union[bool, str]:
            num_result = _num_validator(val, data, host)
            if num_result is not True:
                return num_result

            # Empty values are allowed by opts — skip range check.
            if val == "" and opts.empty():
                return True

            s = str(val)
            if decimal != ".":
                s = s.replace(decimal, ".", 1)

            return opts.message() if float(s) < min else True

        return _validator

    @staticmethod
    def max_num(max: float, decimal: str = ".", cfg: ValidationOptions = None) -> IValidator:
        """Validate that the value is numeric and <= *max*.

        Args:
            max:     The maximum allowed value (inclusive).
            decimal: Decimal separator character (defaults to ``'.'``).
            cfg:     Optional :class:`~datatables_server.ValidationOptions`.

        Returns:
            A validator that rejects values above *max*.
        """
        opts = ValidationOptions.select(cfg)
        _num_validator = Validate.numeric(decimal, opts)

        def _validator(val: Any, data: dict, host: ValidationHost) -> Union[bool, str]:
            num_result = _num_validator(val, data, host)
            if num_result is not True:
                return num_result

            if val == "" and opts.empty():
                return True

            s = str(val)
            if decimal != ".":
                s = s.replace(decimal, ".", 1)

            return opts.message() if float(s) > max else True

        return _validator

    @staticmethod
    def min_max_num(min: float, max: float, decimal: str = ".", cfg: ValidationOptions = None) -> IValidator:
        """Validate that the value is numeric and between *min* and *max* (inclusive).

        Args:
            min:     The minimum allowed value.
            max:     The maximum allowed value.
            decimal: Decimal separator character (defaults to ``'.'``).
            cfg:     Optional :class:`~datatables_server.ValidationOptions`.

        Returns:
            A validator that rejects values outside ``[min, max]``.
        """
        opts = ValidationOptions.select(cfg)
        _num_validator = Validate.numeric(decimal, opts)

        def _validator(val: Any, data: dict, host: ValidationHost) -> Union[bool, str]:
            num_result = _num_validator(val, data, host)
            if num_result is not True:
                return num_result

            if val == "" and opts.empty():
                return True

            s = str(val)
            if decimal != ".":
                s = s.replace(decimal, ".", 1)

            f = float(s)
            return opts.message() if f < min or f > max else True

        return _validator

    # ------------------------------------------------------------------
    # String validators
    # ------------------------------------------------------------------

    @staticmethod
    def email(cfg: ValidationOptions = None) -> IValidator:
        """Validate that the submitted value is a syntactically valid email address.

        Args:
            cfg: Optional :class:`~datatables_server.ValidationOptions`.

        Returns:
            A validator that accepts RFC 5322-ish email addresses.
        """
        opts = ValidationOptions.select(cfg)

        def _validator(val: Any, data: dict, host: ValidationHost) -> Union[bool, str]:
            common = Validate._common(val, opts, data, host)
            if common is not None:
                return opts.message() if common is False else True

            return True if _EMAIL_RE.match(str(val)) else opts.message()

        return _validator

    @staticmethod
    def min_len(min: int, cfg: ValidationOptions = None) -> IValidator:
        """Validate that the string length is >= *min*.

        Args:
            min: The minimum required character count.
            cfg: Optional :class:`~datatables_server.ValidationOptions`.

        Returns:
            A validator that rejects strings shorter than *min* characters.
        """
        opts = ValidationOptions.select(cfg)

        def _validator(val: Any, data: dict, host: ValidationHost) -> Union[bool, str]:
            common = Validate._common(val, opts, data, host)
            if common is not None:
                return opts.message() if common is False else True

            return opts.message() if len(str(val)) < min else True

        return _validator

    @staticmethod
    def max_len(max: int, cfg: ValidationOptions = None) -> IValidator:
        """Validate that the string length is <= *max*.

        Args:
            max: The maximum allowed character count.
            cfg: Optional :class:`~datatables_server.ValidationOptions`.

        Returns:
            A validator that rejects strings longer than *max* characters.
        """
        opts = ValidationOptions.select(cfg)

        def _validator(val: Any, data: dict, host: ValidationHost) -> Union[bool, str]:
            common = Validate._common(val, opts, data, host)
            if common is not None:
                return opts.message() if common is False else True

            return opts.message() if len(str(val)) > max else True

        return _validator

    @staticmethod
    def min_max_len(min: int, max: int, cfg: ValidationOptions = None) -> IValidator:
        """Validate that the string length is between *min* and *max* (inclusive).

        Args:
            min: The minimum required character count.
            max: The maximum allowed character count.
            cfg: Optional :class:`~datatables_server.ValidationOptions`.

        Returns:
            A validator that rejects strings outside ``[min, max]`` characters.
        """
        opts = ValidationOptions.select(cfg)

        def _validator(val: Any, data: dict, host: ValidationHost) -> Union[bool, str]:
            common = Validate._common(val, opts, data, host)
            if common is not None:
                return opts.message() if common is False else True

            length = len(str(val))
            return opts.message() if length < min or length > max else True

        return _validator

    @staticmethod
    def ip(cfg: ValidationOptions = None) -> IValidator:
        """Validate that the submitted value is a dotted-decimal IPv4 address.

        Each octet must be an integer in ``[0, 255]``.

        Args:
            cfg: Optional :class:`~datatables_server.ValidationOptions`.

        Returns:
            A validator that accepts valid IPv4 addresses.
        """
        opts = ValidationOptions.select(cfg)

        def _validator(val: Any, data: dict, host: ValidationHost) -> Union[bool, str]:
            common = Validate._common(val, opts, data, host)
            if common is not None:
                return opts.message() if common is False else True

            parts = str(val).split(".")
            if len(parts) != 4:
                return opts.message()

            for part in parts:
                try:
                    octet = int(part, 10)
                except ValueError:
                    return opts.message()

                # Ensure the string representation matches (no leading zeros etc.)
                if str(octet) != part:
                    return opts.message()

                if octet < 0 or octet > 255:
                    return opts.message()

            return True

        return _validator

    @staticmethod
    def url(cfg: ValidationOptions = None) -> IValidator:
        """Validate that the submitted value is a well-formed HTTP / HTTPS / FTP URL.

        Args:
            cfg: Optional :class:`~datatables_server.ValidationOptions`.

        Returns:
            A validator that accepts URLs with a recognised scheme.
        """
        opts = ValidationOptions.select(cfg)

        def _validator(val: Any, data: dict, host: ValidationHost) -> Union[bool, str]:
            common = Validate._common(val, opts, data, host)
            if common is not None:
                return opts.message() if common is False else True

            return True if _URL_RE.match(str(val)) else opts.message()

        return _validator

    @staticmethod
    def xss(cfg: ValidationOptions = None) -> IValidator:
        """Validate that the value is unchanged after XSS sanitisation.

        Uses the XSS protection configured on the field via
        :meth:`~datatables_server.Field.xss_safety`.  If the sanitised value
        differs from the submitted value the input is considered unsafe.

        Args:
            cfg: Optional :class:`~datatables_server.ValidationOptions`.

        Returns:
            A validator that rejects values that contain XSS content.
        """
        opts = ValidationOptions.select(cfg)

        def _validator(val: Any, data: dict, host: ValidationHost) -> Union[bool, str]:
            common = Validate._common(val, opts, data, host)
            if common is not None:
                return opts.message() if common is False else True

            field = host.field
            return opts.message() if field.xss_safety(val) != val else True

        return _validator

    @staticmethod
    def values(values: List[Any], cfg: ValidationOptions = None) -> IValidator:
        """Validate that the submitted value is in an allowlist.

        Args:
            values: A list of acceptable values.
            cfg:    Optional :class:`~datatables_server.ValidationOptions`.

        Returns:
            A validator that rejects values not present in *values*.
        """
        opts = ValidationOptions.select(cfg)

        def _validator(val: Any, data: dict, host: ValidationHost) -> Union[bool, str]:
            common = Validate._common(val, opts, data, host)
            if common is not None:
                return opts.message() if common is False else True

            return True if val in values else opts.message()

        return _validator

    @staticmethod
    def no_tags(cfg: ValidationOptions = None) -> IValidator:
        """Validate that the submitted value contains no HTML tags.

        Args:
            cfg: Optional :class:`~datatables_server.ValidationOptions`.

        Returns:
            A validator that rejects strings containing ``<...>`` markup.
        """
        opts = ValidationOptions.select(cfg)
        _tag_re = re.compile(r"<.*?>", re.DOTALL)

        def _validator(val: Any, data: dict, host: ValidationHost) -> Union[bool, str]:
            common = Validate._common(val, opts, data, host)
            if common is not None:
                return opts.message() if common is False else True

            return opts.message() if _tag_re.search(str(val)) else True

        return _validator

    # ------------------------------------------------------------------
    # Date validators
    # ------------------------------------------------------------------

    @staticmethod
    def date_format(fmt: str, cfg: ValidationOptions = None) -> IValidator:
        """Validate that the submitted value matches the given :mod:`datetime` format string.

        Args:
            fmt: A :func:`~datetime.datetime.strptime` format string, e.g.
                 ``'%d/%m/%Y'``.
            cfg: Optional :class:`~datatables_server.ValidationOptions`.

        Returns:
            A validator that rejects values that cannot be parsed with *fmt*.
        """
        opts = ValidationOptions.select(cfg)

        def _validator(val: Any, data: dict, host: ValidationHost) -> Union[bool, str]:
            common = Validate._common(val, opts, data, host)
            if common is not None:
                return opts.message() if common is False else True

            try:
                datetime.strptime(str(val), fmt)
                return True
            except (ValueError, TypeError):
                return opts.message()

        return _validator

    # ------------------------------------------------------------------
    # Database validators
    # ------------------------------------------------------------------

    @staticmethod
    def db_unique(
        cfg: ValidationOptions = None,
        column: str = None,
        table: str = None,
        db=None,
    ) -> IValidator:
        """Validate that the value is unique in the database.

        When performing an ``edit`` operation the current row is excluded from
        the uniqueness check so that an unchanged value does not trigger a
        false positive.

        Args:
            cfg:    Optional :class:`~datatables_server.ValidationOptions`.
            column: Database column name to check for uniqueness.  Defaults to
                    the host field's :meth:`~datatables_server.Field.db_field`
                    value.
            table:  Database table to check.  Defaults to the first table
                    returned by :meth:`~datatables_server.Editor.table`.
            db:     SQLAlchemy :class:`~sqlalchemy.engine.Connection` to use.
                    Defaults to the connection on the host editor.

        Returns:
            A validator that returns an error string if the value already exists
            in the database.
        """
        opts = ValidationOptions.select(cfg)

        def _validator(val: Any, data: dict, host: ValidationHost) -> Union[bool, str]:
            common = Validate._common(val, opts, data, host)
            if common is not None:
                return opts.message() if common is False else True

            db_conn = db if db is not None else host.db
            tbl = table if table is not None else host.editor.table()[0]
            col = column if column is not None else host.field.db_field()

            stmt = sa.select(sa.column(col)).select_from(sa.text(tbl)).where(sa.column(col) == val)

            if host.action == "edit":
                pk_obj = host.editor.pkey_to_object(host.id, flat=True)
                for k, v in pk_obj.items():
                    stmt = stmt.where(sa.column(k) != v)

            result = list(db_conn.execute(stmt))
            return opts.message() if result else True

        return _validator

    @staticmethod
    def db_values(
        cfg: ValidationOptions = None,
        column: str = None,
        table: str = None,
        db=None,
        values: List[Any] = None,
    ) -> IValidator:
        """Validate that the submitted value exists in the database (foreign-key check).

        This validator checks that the submitted value is a valid primary key (or
        other lookup column) in a related table.  It can also accept an explicit
        allow-list of values via the *values* parameter — entries in that list
        bypass the database query.

        The table and column are automatically inferred from the field's
        :class:`~datatables_server.Options` configuration when not specified.

        Args:
            cfg:    Optional :class:`~datatables_server.ValidationOptions`.
            column: Column to look up.  Inferred from field options when omitted.
            table:  Table to look up.  Inferred from field options when omitted.
            db:     SQLAlchemy :class:`~sqlalchemy.engine.Connection`.
                    Defaults to the host editor's connection.
            values: Optional list of extra values that are always considered
                    valid (bypasses the database query).

        Returns:
            A validator that rejects values not found in the target table.

        Raises:
            ValueError: If neither the *table* / *column* parameters nor the
                        field's options provide enough information to build the
                        query.
        """
        opts = ValidationOptions.select(cfg)
        _static_values: List[Any] = values or []

        from .options import Options as OptionsClass

        def _validator(val: Any, data: dict, host: ValidationHost) -> Union[bool, str]:
            common = Validate._common(val, opts, data, host)
            field_opts = host.field.options()

            if common is not None:
                return opts.message() if common is False else True

            # Quick exit — value is in the static allow-list.
            if val in _static_values:
                return True

            db_conn = db if db is not None else host.db

            # Resolve table and column from field options if not specified.
            resolved_table = table
            resolved_column = column

            if resolved_table is None and isinstance(field_opts, OptionsClass):
                resolved_table = field_opts.table()

            if resolved_column is None and isinstance(field_opts, OptionsClass):
                resolved_column = field_opts.value()

            if resolved_table is None or resolved_column is None:
                raise ValueError(
                    f"Table or column for database value check is not defined for field "
                    f"'{host.field.name()}'. "
                    "Either pass table/column to db_values() or configure Options on the field."
                )

            stmt = (
                sa.select(sa.column(resolved_column))
                .select_from(sa.text(resolved_table))
                .where(sa.column(resolved_column) == val)
            )

            result = list(db_conn.execute(stmt))
            return opts.message() if not result else True

        return _validator

    # ------------------------------------------------------------------
    # File upload validators
    # ------------------------------------------------------------------

    @staticmethod
    def file_extensions(extns: List[str], msg: str) -> IFileValidator:
        """Validate that an uploaded file has one of the allowed extensions.

        Comparison is case-insensitive.

        Args:
            extns: List of allowed extensions *without* the leading dot,
                   e.g. ``['jpg', 'png', 'gif']``.
            msg:   Error message to return if the extension is not allowed.

        Returns:
            A file validator callable ``(file) -> True | error_string``.
        """
        lower_extns = [e.lower() for e in extns]

        def _validator(file: "IFile", db: Any = None) -> Union[bool, str]:
            extn = file.get("extn", "") if isinstance(file, dict) else getattr(file, "extn", "")
            return True if extn.lower() in lower_extns else msg

        return _validator

    @staticmethod
    def file_size(size: int, msg: str) -> IFileValidator:
        """Validate that an uploaded file does not exceed *size* bytes.

        Args:
            size: Maximum allowed file size in bytes.
            msg:  Error message to return if the file is too large.

        Returns:
            A file validator callable ``(file) -> True | error_string``.
        """

        def _validator(file: "IFile", db: Any = None) -> Union[bool, str]:
            file_size = file.get("size", 0) if isinstance(file, dict) else getattr(file, "size", 0)
            return msg if file_size > size else True

        return _validator

    # ------------------------------------------------------------------
    # Mjoin validators
    # ------------------------------------------------------------------

    @staticmethod
    def mjoin_min_count(size: int, msg: str) -> IMjoinValidator:
        """Validate that at least *size* items are submitted for an Mjoin field.

        Args:
            size: The minimum required number of items.
            msg:  Error message to return when fewer than *size* items are
                  submitted.

        Returns:
            An Mjoin validator callable ``(editor, action, data) -> True | error_string``.
        """

        def _validator(editor: "Editor", action: str, data: List[Any]) -> Union[bool, str]:
            if action in ("create", "edit"):
                return msg if len(data) < size else True
            return True

        return _validator

    @staticmethod
    def mjoin_max_count(size: int, msg: str) -> IMjoinValidator:
        """Validate that at most *size* items are submitted for an Mjoin field.

        Args:
            size: The maximum allowed number of items.
            msg:  Error message to return when more than *size* items are
                  submitted.

        Returns:
            An Mjoin validator callable ``(editor, action, data) -> True | error_string``.
        """

        def _validator(editor: "Editor", action: str, data: List[Any]) -> Union[bool, str]:
            if action in ("create", "edit"):
                return msg if len(data) > size else True
            return True

        return _validator

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _common(
        val: Any,
        opts: ValidationOptions,
        data: Any,
        host: ValidationHost,
    ) -> Optional[bool]:
        """Check common validation conditions before specific field validation.

        This is called at the start of every specific validator.  It handles
        the ``optional``, ``empty``, and ``depends_on`` logic that is shared
        across all validators.

        Args:
            val:  The submitted value for the field.
            opts: The :class:`~datatables_server.ValidationOptions` in effect.
            data: The full submitted row data.
            host: The :class:`~datatables_server.ValidationHost` context.

        Returns:
            * ``True``  — the field passes without further checks (e.g. it is
              optional and no value was submitted, or empty is allowed and the
              value is an empty string).
            * ``False`` — the field fails validation (e.g. required but absent,
              or empty not allowed but empty string submitted).
            * ``None``  — common checks passed; the caller should continue with
              its specific validation logic.
        """
        # Check whether this validator should even run (depends_on / conditional).
        if not Validate._conditional(val, opts, data, host):
            return True

        # --- Error states ---
        if not opts.optional() and val is None:
            # Value must be present but is absent.
            return False

        if val is not None and opts.empty() is False and val == "":
            # Value must be non-empty but is an empty string.
            return False

        # --- Pass states ---
        if opts.optional() and val is None:
            return True

        if opts.empty() is True and val == "":
            return True

        # Fall through to the specific validator.
        return None

    @staticmethod
    def _conditional(
        val: Any,
        opts: ValidationOptions,
        data: Any,
        host: ValidationHost,
    ) -> bool:
        """Check whether a validator should run based on its ``depends_on`` condition.

        Args:
            val:  The field's submitted value.
            opts: The :class:`~datatables_server.ValidationOptions` in effect.
            data: The full submitted row data.
            host: The :class:`~datatables_server.ValidationHost` context.

        Returns:
            ``True`` if the validator should run, ``False`` to skip it.
        """
        if opts is None:
            # No options — no condition — always run.
            return True

        return opts.run_depends(val, data, host)
