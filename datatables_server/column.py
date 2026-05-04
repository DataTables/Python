"""
Read-only column definition for DataTables.

Port of ``column.ts``.  :class:`Column` is a thin wrapper around
:class:`~datatables_server.Field` that pre-configures it as read-only
(``set=False``).  It is intended for columns that are only ever *displayed*
and never written back to the database.
"""

from __future__ import annotations

from typing import Any, Callable, Optional, Union

from .field import Field, SetType


class Column:
    """A read-only field for DataTables column configuration.

    ``Column`` is a simplified version of :class:`~datatables_server.Field`
    that is set to read-only by default (``SetType.NONE``).  It exposes the
    same getter/formatter API as ``Field`` but never writes to the database.

    The underlying :class:`~datatables_server.Field` instance is accessible
    via :meth:`field` for cases where the full ``Field`` API is needed.

    Example::

        from datatables_server import Column, Format

        col = (
            Column('users.created_at', 'createdAt')
            .get_formatter(Format.sql_date_to_format('%d/%m/%Y'))
        )
    """

    def __init__(self, db_field: str, name: str = None) -> None:
        """Create a Column instance.

        Args:
            db_field: Database column name (may include a table prefix,
                      e.g. ``'users.created_at'``).
            name:     JSON / HTTP property name.  Defaults to *db_field* when
                      not supplied.
        """
        self._field = Field(db_field, name)
        self._field.set(SetType.NONE)

    # ------------------------------------------------------------------
    # Proxy helpers
    # ------------------------------------------------------------------

    def _proxy(self, method_name: str, args: list) -> Any:
        """Proxy a method call to the underlying :class:`~datatables_server.Field`.

        If the field returns itself (i.e. the call was used as a setter and the
        field supports chaining), this method returns ``self`` instead so that
        the ``Column`` can be chained.  Otherwise the raw return value is
        forwarded unchanged.

        Args:
            method_name: The name of the :class:`~datatables_server.Field`
                         method to call.
            args:        The positional arguments to forward.

        Returns:
            ``self`` when the proxied call returns the inner :class:`~datatables_server.Field`
            (setter / chaining case), or the actual return value (getter case).
        """
        fn = getattr(self._field, method_name)
        # Strip trailing None sentinels that signal "getter" calls so that the
        # underlying Field method also sees a clean no-arg invocation.
        cleaned_args = args if any(a is not None for a in args) else []
        result = fn(*cleaned_args)

        if result is self._field:
            return self
        return result

    # ------------------------------------------------------------------
    # Public API — proxies to the underlying Field
    # ------------------------------------------------------------------

    def db_field(self, name: str = None) -> Union["Column", str]:
        """Get or set the database column name.

        Args:
            name: The database column name.  Omit to use as a getter.

        Returns:
            The current column name (str) when used as a getter, or ``self``
            for chaining when used as a setter.
        """
        return self._proxy("db_field", [name])

    def field(self) -> Field:
        """Return the underlying :class:`~datatables_server.Field` instance.

        Returns:
            The internal :class:`~datatables_server.Field` used by this
            ``Column``.  Useful when the full ``Field`` API is required.
        """
        return self._field

    def get_formatter(self, formatter: Callable = None) -> Union["Column", Callable]:
        """Get or set the formatter applied when reading data from the database.

        When the data has been retrieved from the database it is passed through
        this formatter before being sent to the client.

        Args:
            formatter: A callable ``(val, row_data) -> Any``.  Omit to use as
                       a getter.

        Returns:
            The current formatter callable when used as a getter, or ``self``
            for chaining when used as a setter.
        """
        return self._proxy("get_formatter", [formatter])

    def get_value(self, val: Any = None) -> Union["Column", Any]:
        """Get or set a fixed value to send to the client (overrides db value).

        Args:
            val: The fixed value to use.  Omit to use as a getter.

        Returns:
            The current get value when used as a getter, or ``self`` for
            chaining when used as a setter.
        """
        return self._proxy("get_value", [val])

    def name(self, name: str = None) -> Union["Column", str]:
        """Get or set the column's JSON / HTTP property name.

        Args:
            name: The name string.  Omit to use as a getter.

        Returns:
            The current name string when used as a getter, or ``self`` for
            chaining when used as a setter.
        """
        return self._proxy("name", [name])

    def search_builder_options(self, sb_opts=None) -> Union["Column", Any]:
        """Get or set SearchBuilder options for this column.

        Args:
            sb_opts: A :class:`~datatables_server.SearchBuilderOptions` instance.
                     Omit to use as a getter.

        Returns:
            The current :class:`~datatables_server.SearchBuilderOptions` when
            used as a getter, or ``self`` for chaining when used as a setter.
        """
        return self._proxy("search_builder_options", [sb_opts])

    def search_pane_options(self, sp_opts=None) -> Union["Column", Any]:
        """Get or set SearchPanes options for this column.

        Args:
            sp_opts: A :class:`~datatables_server.SearchPaneOptions` instance.
                     Omit to use as a getter.

        Returns:
            The current :class:`~datatables_server.SearchPaneOptions` when used
            as a getter, or ``self`` for chaining when used as a setter.
        """
        return self._proxy("search_pane_options", [sp_opts])

    def column_control(self, options=None) -> Union["Column", Any]:
        """Get or set ColumnControl options for this column.

        Args:
            options: An :class:`~datatables_server.Options` instance that
                     provides the choices shown in the ColumnControl widget.
                     Omit to use as a getter.

        Returns:
            The current :class:`~datatables_server.Options` instance when used
            as a getter, or ``self`` for chaining when used as a setter.
        """
        return self._proxy("column_control", [options])
