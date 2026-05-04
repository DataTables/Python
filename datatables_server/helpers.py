"""
helpers.py – Utility formatter factories for use with the DataTables server library.

This module provides helpers that address the gap between Python's
:mod:`datetime` ``strftime``/``strptime`` directives and the MomentJS
format conventions used by the DataTables / Editor client-side libraries.

The main problem
----------------
MomentJS uses ``D`` (unpadded day) and ``M`` (unpadded month) in format strings
like ``D MMM YYYY``, ``M/D/YYYY``, etc.  Python's ``strftime`` has no portable
equivalent — ``%-d`` and ``%-m`` are Linux-only ``glibc`` extensions that are
not available on macOS, Windows, or some Linux environments.

The solution
------------
Use the padded directives (``%d``, ``%m``) everywhere — ``strptime`` already
accepts both padded *and* unpadded input with these directives, so parsing is
fine.  For *output*, wrap the formatter with :func:`unpadded_format` to strip
the unwanted leading zeros from whichever components need it.
"""

from __future__ import annotations

from typing import Any, Callable, List, Optional, Tuple, Union

# A formatter callable: (value, row_data) -> Any
Formatter = Callable[[Any, dict], Any]


def unpadded_format(
    base_formatter: Formatter,
    components: Union[List[Tuple[str, int]], Tuple[str, int]],
) -> Formatter:
    """Wrap *base_formatter* to strip leading zeros from specified date components.

    Python's ``strftime`` always zero-pads ``%d`` and ``%m`` (e.g. ``"04"``
    for the 4th).  MomentJS ``D`` / ``M`` produce unpadded output (``"4"``).
    This factory wraps any formatter to post-process its string output and
    remove leading zeros from the nominated components.

    The *components* argument describes how to locate each component that
    needs de-padding.  Each entry is a ``(separator, index)`` pair:

    * **separator** – the string used to split the formatted date string into
      parts (e.g. ``"/"`` for ``"04/02/2026"``, ``" "`` for ``"02 May 2026"``).
    * **index** – the zero-based position of the component to de-pad after
      splitting on *separator*.

    For formats with a **single separator**, pass one tuple directly::

        # %m/%d/%Y  ->  M/D/YYYY  (strip zeros from month[0] and day[1])
        fmt = unpadded_format(
            Format.sql_date_to_format("%m/%d/%Y"),
            [("/", 0), ("/", 1)],
        )

    For formats where the day follows a weekday name (``%A %d %B %Y``)::

        # %A %d %B %Y  ->  dddd D MMMM YYYY  (strip zero from day[1])
        fmt = unpadded_format(
            Format.sql_date_to_format("%A %d %B %Y"),
            [(" ", 1)],
        )

    For formats like ``%a, %d %B %Y`` where the day is after the comma-space::

        # %a, %d %B %Y  ->  ddd, D MMMM YYYY  (strip zero from day[1] of space-split)
        fmt = unpadded_format(
            Format.sql_date_to_format("%a, %d %B %Y"),
            [(" ", 1)],
        )

    For ``%d %b %Y %H:%M`` (day at position 0 of space-split)::

        fmt = unpadded_format(
            Format.date_time("%Y-%m-%d %H:%M:%S", "%d %b %Y %H:%M"),
            [(" ", 0)],
        )

    The input value is left entirely unchanged — de-padding only affects the
    *output* of the formatter.  Because ``strptime`` with ``%d`` / ``%m``
    already accepts both padded and unpadded input, the same parse format string
    is used for both validation and the set formatter without any modification.

    Args:
        base_formatter: Any formatter callable ``(val, data) -> str | None``
            whose string output should have leading zeros stripped from one or
            more components.
        components: A single ``(separator, index)`` tuple, or a list of them.
            Each entry identifies one component of the formatted string to
            de-pad.  When the same separator appears multiple times in the
            list, the string is split on that separator once and all nominated
            indices are de-padded in a single pass.

    Returns:
        A new :data:`Formatter` callable with the same signature as
        *base_formatter* but with leading zeros stripped from the nominated
        components.  ``None`` input values are passed through as ``None``.

    Examples::

        from datatables_server import Format
        from datatables_server.helpers import unpadded_format

        # M/D/YYYY (unpadded month and day, slash-separated)
        get_fmt = unpadded_format(Format.sql_date_to_format("%m/%d/%Y"), [("/", 0), ("/", 1)])
        get_fmt("2026-04-02", {})  # -> "4/2/2026"

        # D MMM YYYY (unpadded day, space-separated, day at index 0)
        get_fmt2 = unpadded_format(Format.sql_date_to_format("%d %b %Y"), [(" ", 0)])
        get_fmt2("2026-04-02", {})  # -> "2 Apr 2026"

        # ddd, D MMMM YYYY (unpadded day at index 1 of space-split)
        get_fmt3 = unpadded_format(Format.sql_date_to_format("%a, %d %B %Y"), [(" ", 1)])
        get_fmt3("2026-04-02", {})  # -> "Thu, 2 April 2026"
    """
    # Normalise to a list of (sep, idx) tuples
    if isinstance(components, tuple) and len(components) == 2 and isinstance(components[0], str):
        # Single tuple passed directly, e.g. (" ", 0)
        norm: List[Tuple[str, int]] = [components]  # type: ignore[list-item]
    else:
        norm = list(components)  # type: ignore[arg-type]

    def _formatter(val: Any, data: dict) -> Optional[str]:
        result: Optional[str] = base_formatter(val, data)
        if result is None:
            return None

        # Group indices by separator for efficiency
        by_sep: dict = {}
        for sep, idx in norm:
            by_sep.setdefault(sep, []).append(idx)

        for sep, indices in by_sep.items():
            parts = result.split(sep)
            for idx in indices:
                if 0 <= idx < len(parts):
                    parts[idx] = parts[idx].lstrip("0") or "0"
            result = sep.join(parts)

        return result

    return _formatter
