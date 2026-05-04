"""
Formatter factories for use with ``Field.get_formatter()`` and ``Field.set_formatter()``.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable, List, Union


def _to_time_string(val: Any) -> str:
    """Convert *val* to a zero-padded ``HH:MM:SS`` string for ``strptime``.

    MySQL / MariaDB TIME columns are returned by PyMySQL as :class:`datetime.timedelta`
    objects rather than strings.  This helper normalises both ``timedelta`` and
    plain strings to the ``HH:MM:SS`` format expected by ``strptime``.
    """
    if isinstance(val, timedelta):
        total = int(val.total_seconds())
        h, remainder = divmod(abs(total), 3600)
        m, s = divmod(remainder, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"
    return str(val)


try:
    from dateutil import parser as _dateutil_parser

    def _parse_loose(val: str) -> datetime:
        """Parse *val* using dateutil, which tolerates trailing time components."""
        return _dateutil_parser.parse(val)

except ImportError:  # pragma: no cover – dateutil is optional

    def _parse_loose(val: str) -> datetime:  # type: ignore[misc]
        """Fallback: strip a trailing time component before parsing."""
        # Accept 'YYYY-MM-DD', 'YYYY-MM-DD HH:MM:SS', 'YYYY-MM-DDTHH:MM:SS', etc.
        return datetime.fromisoformat(val.split("T")[0].split(" ")[0])


# A formatter is any callable that accepts (value, row_data) and returns a value.
Formatter = Callable[[Any, dict], Any]


class Format:
    """Formatter factories for :meth:`~datatables_server.Field.get_formatter` and
    :meth:`~datatables_server.Field.set_formatter`.

    Each static method returns a :data:`Formatter` callable
    ``(val: Any, data: dict) -> Any`` that can be passed directly to the field
    configuration methods.

    Date / time formatting uses :mod:`datetime` ``strftime`` / ``strptime``
    format strings (e.g. ``'%d/%m/%Y'``) rather than the MomentJS format
    strings used in the original TypeScript library.

    Example::

        field = Field('birth_date')
        field.get_formatter(Format.sql_date_to_format('%d/%m/%Y'))
        field.set_formatter(Format.format_to_sql_date('%d/%m/%Y'))
    """

    @staticmethod
    def sql_date_to_format(fmt: str) -> Formatter:
        """Return a formatter that converts a SQL date (``YYYY-MM-DD``) to *fmt*.

        Typically used as a **get** formatter so that dates are presented in a
        localised format on the client side.

        Args:
            fmt: A :func:`~datetime.datetime.strftime` format string describing
                the desired output format (e.g. ``'%d/%m/%Y'``).

        Returns:
            A :data:`Formatter` callable ``(val, data) -> str | None``.
        """

        def _formatter(val: Any, data: dict) -> Any:
            if val is None:
                return None
            dt = _parse_loose(str(val))
            return dt.strftime(fmt)

        return _formatter

    @staticmethod
    def format_to_sql_date(fmt: str) -> Formatter:
        """Return a formatter that converts a date in *fmt* to SQL ``YYYY-MM-DD``.

        Typically used as a **set** formatter so that client-supplied dates are
        normalised before being written to the database.

        Args:
            fmt: A :func:`~datetime.datetime.strptime` format string describing
                the input format (e.g. ``'%d/%m/%Y'``).

        Returns:
            A :data:`Formatter` callable ``(val, data) -> str | None``.
        """

        def _formatter(val: Any, data: dict) -> Any:
            if val is None or val == "":
                return None
            dt = datetime.strptime(str(val), fmt)
            return dt.strftime("%Y-%m-%d")

        return _formatter

    @staticmethod
    def date_time(from_fmt: str, to_fmt: str) -> Formatter:
        """Return a formatter that re-formats a datetime string.

        Args:
            from_fmt: :func:`~datetime.datetime.strptime` format of the *input*.
            to_fmt:   :func:`~datetime.datetime.strftime` format of the *output*.

        Returns:
            A :data:`Formatter` callable ``(val, data) -> str | None``.
        """

        def _formatter(val: Any, data: dict) -> Any:
            if val is None:
                return None
            dt = datetime.strptime(_to_time_string(val), from_fmt)
            return dt.strftime(to_fmt)

        return _formatter

    @staticmethod
    def explode(delimiter: str = "|") -> Formatter:
        """Return a formatter that splits a delimited string into a list.

        Useful for converting pipe-separated (or otherwise delimited) database
        values into a list of checked values for checkbox fields.

        Args:
            delimiter: The character (or string) to split on.  Defaults to
                ``'|'``.

        Returns:
            A :data:`Formatter` callable ``(val, data) -> list``.
        """

        def _formatter(val: Any, data: dict) -> List[Any]:
            return str(val).split(delimiter)

        return _formatter

    @staticmethod
    def implode(delimiter: str = "|") -> Formatter:
        """Return a formatter that joins a list into a delimited string.

        The inverse of :meth:`explode`.  Useful for converting a list of
        checkbox selections into a single string for database storage.

        Args:
            delimiter: The character (or string) to join on.  Defaults to
                ``'|'``.

        Returns:
            A :data:`Formatter` callable ``(val, data) -> str``.
        """

        def _formatter(val: Any, data: dict) -> str:
            if isinstance(val, (list, tuple)):
                return delimiter.join(str(v) for v in val)
            return str(val)

        return _formatter

    @staticmethod
    def if_empty(empty_value: Any) -> Formatter:
        """Return a formatter that substitutes *empty_value* for empty strings.

        HTTP form submissions cannot represent SQL ``NULL``, so empty strings
        often overlap with null.  This formatter maps ``''`` to *empty_value*
        (typically ``None``), leaving all other values untouched.

        Args:
            empty_value: The value to return when the input is ``''``.

        Returns:
            A :data:`Formatter` callable ``(val, data) -> Any``.
        """

        def _formatter(val: Any, data: dict) -> Any:
            return empty_value if val == "" else val

        return _formatter

    @staticmethod
    def from_decimal_char(char: str = ",") -> Formatter:
        """Return a formatter that normalises a decimal separator to a period.

        Useful for regions where a comma is used as the decimal character.
        Typically used as a **set** formatter.

        Args:
            char: The decimal character to replace with ``'.'``.  Defaults to
                ``','``.

        Returns:
            A :data:`Formatter` callable ``(val, data) -> str``.
        """

        def _formatter(val: Any, data: dict) -> str:
            return str(val).replace(char, ".", 1)

        return _formatter

    @staticmethod
    def to_decimal_char(char: str = ",") -> Formatter:
        """Return a formatter that replaces a period decimal separator with *char*.

        The inverse of :meth:`from_decimal_char`.  Typically used as a **get**
        formatter for display purposes.

        Args:
            char: The decimal character to substitute for ``'.'``.  Defaults to
                ``','``.

        Returns:
            A :data:`Formatter` callable ``(val, data) -> str``.
        """

        def _formatter(val: Any, data: dict) -> str:
            return str(val).replace(".", char, 1)

        return _formatter
