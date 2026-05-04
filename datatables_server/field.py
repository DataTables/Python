"""
Field definition for DataTables Editor.

Each database column used with Editor is described with
a :class:`Field` instance — it tells Editor the column name, how to format the
data, and whether to read and/or write it.
"""

from __future__ import annotations

import html
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Union, TYPE_CHECKING

from .nested_data import NestedData

if TYPE_CHECKING:
    from .editor import Editor
    from .options import Options
    from .search_builder_options import SearchBuilderOptions
    from .search_pane_options import SearchPaneOptions
    from .upload import Upload
    from .validation_host import ValidationHost

# Formatter: called as formatter(value, row_data) -> transformed_value
Formatter = Callable[[Any, dict], Any]

# Validator: called as validator(value, row_data, host) -> True | error_string
Validator = Callable[[Any, dict, "ValidationHost"], Union[bool, str]]


class SetType(Enum):
    """Controls when a field's value is written to the database.

    Attributes:
        NONE:   Never write this field to the database.
        BOTH:   Write on both create and edit operations (default).
        CREATE: Write only when creating a new row.
        EDIT:   Write only when editing an existing row.
    """

    NONE = "none"
    BOTH = "both"
    CREATE = "create"
    EDIT = "edit"


class Field(NestedData):
    """Describes a database column for DataTables Editor.

    Each database column used with Editor is described with a ``Field``
    instance.  It tells Editor the column name, how to format the data, and
    whether to read and/or write this column.

    Fields are used with :meth:`~datatables_server.Editor.field` and
    :meth:`~datatables_server.Mjoin.field` to describe what columns should be
    interacted with.

    Example::

        from datatables_server import Field, Format, Validate

        field = (
            Field('birth_date', 'birthDate')
            .get_formatter(Format.sql_date_to_format('%d/%m/%Y'))
            .set_formatter(Format.format_to_sql_date('%d/%m/%Y'))
            .validator(Validate.required())
        )
    """

    # Expose SetType as a class attribute for backwards-compat / convenience.
    SetType = SetType

    def __init__(self, db_field: str, name: str = None) -> None:
        """Create a Field instance.

        Args:
            db_field: Database column name (may include a table prefix,
                      e.g. ``'users.first_name'``).
            name:     JSON / HTTP property name used when communicating with
                      the client.  Defaults to *db_field* when not supplied.
        """
        super().__init__()

        self._column_control: Optional["Options"] = None
        self._db_field: str = ""
        self._get: bool = True
        self._get_formatter: Optional[Formatter] = None
        self._get_value: Any = None  # sentinel: None means "not set"
        self._get_value_set: bool = False  # tracks explicit set_value calls
        self._http: bool = True
        self._opts: Optional["Options"] = None
        self._name: str = ""
        self._sp_opts: Optional["SearchPaneOptions"] = None
        self._sb_opts: Optional["SearchBuilderOptions"] = None
        self._set: SetType = SetType.BOTH
        self._set_formatter: Optional[Formatter] = None
        self._set_value: Any = None
        self._set_value_set: bool = False  # tracks explicit set_value calls
        self._validators: List[Dict[str, Any]] = []  # list of {validator, set_formatted}
        self._upload_inst: Optional["Upload"] = None
        self._xss_fn: Optional[Callable] = None
        self._xss_format: bool = True

        # Apply constructor args — mirrors the TS constructor logic.
        if name:
            self.name(name)
            self.db_field(db_field)
        else:
            # Single-parameter form: both name and db_field are the same.
            self.name(db_field)
            self.db_field(db_field)

    # ------------------------------------------------------------------
    # Public fluent API — getters return the stored value; setters return self.
    # ------------------------------------------------------------------

    def column_control(self, options: "Options" = None) -> Union["Field", "Options"]:
        """Get or set ColumnControl options for this field.

        Args:
            options: An :class:`~datatables_server.Options` instance that
                     provides the choices shown in the ColumnControl widget.
                     Omit to use as a getter.

        Returns:
            The current :class:`~datatables_server.Options` instance when used
            as a getter, or ``self`` for chaining when used as a setter.
        """
        if options is None:
            return self._column_control

        self._column_control = options
        return self

    def db_field(self, db_field: str = None) -> Union["Field", str]:
        """Get or set the database column name.

        Args:
            db_field: The database column name (e.g. ``'first_name'`` or
                      ``'users.first_name'``).  Omit to use as a getter.

        Returns:
            The current column name (str) when used as a getter, or ``self``
            for chaining when used as a setter.
        """
        if db_field is None:
            return self._db_field

        self._db_field = db_field
        return self

    def get(self, flag: bool = None) -> Union["Field", bool]:
        """Get or set whether this field is readable from the database.

        When ``False`` the field will never be included in ``GET`` responses
        sent to the client.

        Args:
            flag: ``True`` (default) to allow reading; ``False`` to suppress.
                  Omit to use as a getter.

        Returns:
            Current boolean value when used as a getter, or ``self`` for
            chaining when used as a setter.
        """
        if flag is None:
            return self._get

        self._get = flag
        return self

    def get_formatter(self, formatter: Formatter = None) -> Union["Field", Formatter]:
        """Get or set the formatter applied when reading data from the database.

        When the data has been retrieved from the database it is passed through
        this formatter before being sent to the client.  This is useful for
        converting database date formats into localised display formats.

        Args:
            formatter: A callable ``(val, row_data) -> Any``.  Omit to use as
                       a getter.

        Returns:
            The current formatter callable when used as a getter, or ``self``
            for chaining when used as a setter.
        """
        if formatter is None:
            return self._get_formatter

        self._get_formatter = formatter
        return self

    def get_value(self, val: Any = None, _sentinel: object = None) -> Union["Field", Any]:
        """Get or set a fixed value to send to the client (overrides db value).

        When set, this value is used in all ``GET`` responses regardless of
        what the database contains.  The value may be a plain value or a
        zero-argument callable that is invoked on each request.

        Args:
            val: The fixed value to use.  Omit to use as a getter.

        Returns:
            The current get value when used as a getter, or ``self`` for
            chaining when used as a setter.

        Note:
            Because ``None`` is a legitimate value to store, pass a sentinel to
            distinguish a genuine ``get_value(None)`` set from a no-argument
            getter call.  In practice, simply call ``get_value()`` with no args
            to read, and ``get_value(<any value>)`` to write.
        """
        # No-arg call → getter
        if val is None and _sentinel is None and not self._get_value_set:
            return self._get_value

        self._get_value = val
        self._get_value_set = True
        return self

    def http(self, flag: bool = None) -> Union["Field", bool]:
        """Get or set whether this field can be read via HTTP (externally).

        When ``False`` the field's value is withheld from HTTP responses even
        if it would otherwise be sent.

        Args:
            flag: ``True`` (default) to allow HTTP access; ``False`` to block.
                  Omit to use as a getter.

        Returns:
            Current boolean value when used as a getter, or ``self`` for
            chaining when used as a setter.
        """
        if flag is None:
            return self._http

        self._http = flag
        return self

    def name(self, name: str = None) -> Union["Field", str]:
        """Get or set the field's JSON / HTTP name.

        The name is typically the same as the database column name.  It
        controls the key used in the JSON payload sent to and received from the
        client-side DataTables / Editor JavaScript.

        Args:
            name: The name string.  Omit to use as a getter.

        Returns:
            The current name string when used as a getter, or ``self`` for
            chaining when used as a setter.
        """
        if name is None:
            return self._name

        self._name = name
        return self

    def options(
        self,
        opts=None,
        value: str = "",
        label: str = "",
        condition: Any = None,
        format: Callable = None,
        order: Union[str, bool] = None,
    ) -> Union["Field", "Options"]:
        """Get or set options for select / radio / checkbox fields.

        Can be called in several ways:

        * **No arguments** — returns the current :class:`~datatables_server.Options`
          instance.
        * **An** :class:`~datatables_server.Options` **instance** — sets it directly.
        * **A callable** — wraps it as a custom options function using
          :meth:`~datatables_server.Options.fn`.
        * **A table name string** (plus optional value/label/condition/format/order
          strings) — creates and configures a new
          :class:`~datatables_server.Options` instance from the parameters.

        Args:
            opts:      An :class:`~datatables_server.Options` instance, a
                       callable, or a table name string.
            value:     Column name for option values (used with table name).
            label:     Column name for option labels (used with table name).
            condition: SQL WHERE condition (used with table name).
            format:    Label formatter callable (used with table name).
            order:     ORDER BY clause or ``False`` to disable sorting.

        Returns:
            The current options object when used as a getter, or ``self`` for
            chaining when used as a setter.
        """
        # Lazy import to avoid circular dependency at module level.
        from .options import Options

        if opts is None:
            return self._opts

        if isinstance(opts, Options):
            self._opts = opts
        elif callable(opts):
            self._opts = Options().fn(opts)
        else:
            # opts is a table name string
            self._opts = Options().table(opts).value(value).label(label)

            if condition is not None:
                self._opts.where(condition)

            if format is not None:
                self._opts.render(format)

            if order is not None:
                self._opts.order(order)

        return self

    def search_builder_options(self, sb_opts: "SearchBuilderOptions" = None) -> Union["Field", "SearchBuilderOptions"]:
        """Get or set SearchBuilder options for this field.

        Args:
            sb_opts: A :class:`~datatables_server.SearchBuilderOptions` instance.
                     Omit to use as a getter.

        Returns:
            The current :class:`~datatables_server.SearchBuilderOptions` instance
            when used as a getter, or ``self`` for chaining when used as a setter.
        """
        if sb_opts is None:
            return self._sb_opts

        self._sb_opts = sb_opts
        return self

    def search_pane_options(self, sp_opts: "SearchPaneOptions" = None) -> Union["Field", "SearchPaneOptions"]:
        """Get or set SearchPanes options for this field.

        Args:
            sp_opts: A :class:`~datatables_server.SearchPaneOptions` instance.
                     Omit to use as a getter.

        Returns:
            The current :class:`~datatables_server.SearchPaneOptions` instance
            when used as a getter, or ``self`` for chaining when used as a setter.
        """
        if sp_opts is None:
            return self._sp_opts

        self._sp_opts = sp_opts
        return self

    def set(self, flag: Union[bool, SetType] = None) -> Union["Field", SetType]:
        """Get or set when this field's value is written to the database.

        Controls whether the field participates in ``create``, ``edit``, both,
        or neither database writes.

        Args:
            flag: A :class:`SetType` value, ``True`` (equivalent to
                  ``SetType.BOTH``), or ``False`` (equivalent to
                  ``SetType.NONE``).  Omit to use as a getter.

        Returns:
            The current :class:`SetType` when used as a getter, or ``self``
            for chaining when used as a setter.
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

    def set_formatter(self, formatter: Formatter = None) -> Union["Field", Formatter]:
        """Get or set the formatter applied when writing data to the database.

        When data has been submitted from the client it is passed through this
        formatter before being written to the database.  This is useful for
        converting user-facing date formats back into the SQL-compatible format.

        Args:
            formatter: A callable ``(val, row_data) -> Any``.  Omit to use as
                       a getter.

        Returns:
            The current formatter callable when used as a getter, or ``self``
            for chaining when used as a setter.
        """
        if formatter is None:
            return self._set_formatter

        self._set_formatter = formatter
        return self

    def set_value(self, val: Any = None, _sentinel: object = None) -> Union["Field", Any]:
        """Get or set a fixed value to write to the database (overrides submitted value).

        When set, this value is used for database writes regardless of what the
        client submitted.  The value may be a plain value or a zero-argument
        callable invoked on each write.

        Args:
            val: The fixed value to use.  Omit to use as a getter.

        Returns:
            The current set value when used as a getter, or ``self`` for
            chaining when used as a setter.
        """
        if val is None and _sentinel is None and not self._set_value_set:
            return self._set_value

        self._set_value = val
        self._set_value_set = True
        return self

    def upload(self, upload: "Upload" = None) -> Union["Field", "Upload"]:
        """Get or set an Upload instance for file upload handling.

        Args:
            upload: An :class:`~datatables_server.Upload` instance that
                    controls how file uploads are stored.  Omit to use as a
                    getter.

        Returns:
            The current :class:`~datatables_server.Upload` instance when used
            as a getter, or ``self`` for chaining when used as a setter.
        """
        if upload is None:
            return self._upload_inst

        self._upload_inst = upload
        return self

    def validator(self, validator: Validator = None, set_formatted: bool = False) -> Union["Field", List[Validator]]:
        """Get the list of validators, or add a new validator.

        Multiple validators can be added by calling this method multiple times.
        They are executed in order; the first failure stops the chain.

        Args:
            validator:     A callable ``(val, data, host) -> True | str``.
                           Omit to use as a getter that returns the list of
                           currently registered validator callables.
            set_formatted: When ``True`` the *formatted* (set-formatter applied)
                           value is passed to the validator instead of the raw
                           submitted value.

        Returns:
            A list of validator callables when used as a getter, or ``self``
            for chaining when a validator is added.
        """
        if validator is None:
            return [v["validator"] for v in self._validators]

        self._validators.append({"validator": validator, "set_formatted": set_formatted})
        return self

    def xss(self, flag: Union[bool, Callable] = None) -> Union["Field", Callable]:
        """Get or set XSS protection for this field.

        By default XSS protection is applied using Python's
        :func:`html.escape`.  This can be replaced with a custom callable,
        disabled entirely by passing ``False``, or re-enabled (with the
        default) by passing ``True``.

        Args:
            flag: ``True`` to enable default HTML escaping, ``False`` to
                  disable XSS protection, or a callable ``(val) -> val`` that
                  performs custom sanitisation.  Omit to use as a getter.

        Returns:
            The current XSS callable (or ``None`` if disabled) when used as a
            getter, or ``self`` for chaining when used as a setter.
        """
        if flag is None:
            return self._xss_fn

        if flag is True:
            self._xss_fn = html.escape
        elif flag is False:
            self._xss_fn = None
        else:
            self._xss_fn = flag

        return self

    # ------------------------------------------------------------------
    # Internal methods used by Editor — not part of the public API.
    # ------------------------------------------------------------------

    def apply(self, action: str, data: Any = None) -> bool:
        """Determine whether this field should participate in the given action.

        Called internally by :class:`~datatables_server.Editor` before reading
        or writing a field's value.

        Args:
            action: One of ``'get'``, ``'create'``, or ``'edit'``.
            data:   The submitted row data dict (used to check if the field was
                    included in the submission).

        Returns:
            ``True`` if the field should be applied for the action, ``False``
            otherwise.
        """
        if action == "get":
            return self._get

        # For 'create' / 'edit' — first check the set-type flags.
        if action == "create" and self._set in (SetType.NONE, SetType.EDIT):
            return False

        if action == "edit" and self._set in (SetType.NONE, SetType.CREATE):
            return False

        # If there is an explicit set_value we always apply the field.
        if self._set_value_set:
            return True

        # Otherwise the field must have been present in the submitted data.
        if not self._prop_exists(self._name, data):
            return False

        return True

    def search_builder_options_exec(
        self,
        field: "Field",
        editor: "Editor",
        http: Any,
        fields: List["Field"],
        left_join: List,
        db: Any,
    ) -> Any:
        """Execute SearchBuilder options retrieval.

        Called internally by :class:`~datatables_server.Editor` when the client
        requests SearchBuilder option lists.

        Args:
            field:      This field instance.
            editor:     The owning :class:`~datatables_server.Editor`.
            http:       The parsed HTTP request object.
            fields:     All fields registered on the editor.
            left_join:  Left-join configuration list.
            db:         Active SQLAlchemy :class:`~sqlalchemy.engine.Connection`.

        Returns:
            A list of option dicts, or ``False`` if no SearchBuilder options
            are configured.
        """
        from .search_builder_options import SearchBuilderOptions

        if isinstance(self._sb_opts, SearchBuilderOptions):
            return self._sb_opts.exec(field, editor, http, fields, left_join)
        elif callable(self._sb_opts):
            return self._sb_opts(db, editor)

        return False

    def search_pane_options_exec(
        self,
        field: "Field",
        editor: "Editor",
        http: Any,
        fields: List["Field"],
        left_join: List,
        db: Any,
    ) -> Any:
        """Execute SearchPanes options retrieval.

        Called internally by :class:`~datatables_server.Editor` when the client
        requests SearchPanes option lists.

        Args:
            field:      This field instance.
            editor:     The owning :class:`~datatables_server.Editor`.
            http:       The parsed HTTP request object.
            fields:     All fields registered on the editor.
            left_join:  Left-join configuration list.
            db:         Active SQLAlchemy :class:`~sqlalchemy.engine.Connection`.

        Returns:
            A list of option dicts, or ``False`` if no SearchPanes options are
            configured.
        """
        from .search_pane_options import SearchPaneOptions

        if isinstance(self._sp_opts, SearchPaneOptions):
            return self._sp_opts.exec(field, editor, http, fields, left_join)
        elif callable(self._sp_opts):
            return self._sp_opts(db, editor)

        return False

    def val(self, direction: str, data: Any) -> Any:
        """Retrieve or prepare the field's value with formatting applied.

        Called internally by :class:`~datatables_server.Editor` when building
        a ``GET`` response or preparing a database write.

        Args:
            direction: ``'get'`` to read a value from a database row dict, or
                       ``'set'`` to read a submitted value from the HTTP payload.
            data:      For ``'get'``: the database row dict (keyed by column name).
                       For ``'set'``: the submitted data dict (keyed by field name).

        Returns:
            The (optionally formatted) value for this field.
        """
        if direction == "get":
            if self._get_value_set:
                val = self._get_value() if callable(self._get_value) else self._get_value
            else:
                val = data.get(self._db_field) if isinstance(data, dict) else None

            return self._format(val, data, self._get_formatter)

        # direction == 'set'
        if self._set_value_set:
            val = self._set_value() if callable(self._set_value) else self._set_value
        else:
            val = self._read_prop(self._name, data)

        return self._format(val, data, self._set_formatter)

    def validate(self, data: dict, editor: "Editor", id: str, action: str) -> Union[bool, str]:
        """Run all registered validators against the submitted value.

        Called internally by :class:`~datatables_server.Editor` during create
        and edit operations.

        Args:
            data:   The full submitted row data dict.
            editor: The owning :class:`~datatables_server.Editor` instance.
            id:     The row ID being edited (empty string for create).
            action: The current action (``'create'`` or ``'edit'``).

        Returns:
            ``True`` if all validators pass, or the first error message string
            returned by a failing validator.
        """
        from .validation_host import ValidationHost

        if not self._validators:
            return True

        val = self._read_prop(self.name(), data)

        host = ValidationHost(
            action=action or "",
            id=id,
            field=self,
            editor=editor,
            db=editor.db(),
        )

        for entry in self._validators:
            validator_fn = entry["validator"]
            test_val = self.val("set", data) if entry["set_formatted"] else val

            result = validator_fn(test_val, data, host)

            if result is not True:
                return result

        return True

    def write(self, out: dict, src_data: dict) -> None:
        """Write this field's formatted get value into the output dict.

        Called internally by :class:`~datatables_server.Editor` when building
        the row data dicts sent to the client.

        Args:
            out:      The output dict to write into (may use dot-notation nesting
                      via :meth:`~datatables_server.NestedData._write_prop`).
            src_data: The source database row dict to read the raw value from.
        """
        self._write_prop(out, self.name(), self.val("get", src_data))

    def xss_safety(self, val: Any) -> Any:
        """Apply XSS protection to a submitted value.

        If XSS protection is disabled (``self._xss_fn`` is ``None``) the value
        is returned unchanged.  Lists are sanitised element-by-element.

        Args:
            val: The raw submitted value.

        Returns:
            The sanitised value (or the original value if XSS is disabled).
        """
        if not self._xss_fn:
            return val

        if isinstance(val, list):
            return [self._xss_fn(item) for item in val]

        return self._xss_fn(val)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _format(self, val: Any, data: Any, formatter: Optional[Formatter]) -> Any:
        """Apply *formatter* to *val*, or return *val* unchanged if no formatter.

        Args:
            val:       The value to format.
            data:      The full row data dict (passed as the second argument to
                       formatter callables).
            formatter: The formatter callable, or ``None``.

        Returns:
            The formatted value, or *val* if *formatter* is ``None``.
        """
        return formatter(val, data) if formatter else val
