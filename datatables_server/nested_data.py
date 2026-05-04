"""
Base class providing nested property read/write using dot-notation strings.
"""

from __future__ import annotations

from typing import Any


class NestedData:
    """Base class providing nested property access via dot-notation strings.

    Any class that needs to read from or write to deeply nested dict structures
    using ``'parent.child.leaf'``-style property names should extend this class.

    Example::

        class MyClass(NestedData):
            def example(self):
                data = {'a': {'b': {'c': 42}}}
                val = self._read_prop('a.b.c', data)   # => 42
                self._write_prop({}, 'x.y', 99)         # => {'x': {'y': 99}}
    """

    # ------------------------------------------------------------------
    # Public (protected) helpers
    # ------------------------------------------------------------------

    def _prop_exists(self, name: str, data: Any) -> bool:
        """Check whether a nested property exists in *data*.

        Args:
            name: Dot-notation property path, e.g. ``'address.city'``.
            data: The dict (or dict-like object) to inspect.

        Returns:
            ``True`` if the property exists and is not ``None`` / undefined,
            ``False`` otherwise.
        """
        if data is None:
            return False

        if "." not in name:
            return name in data and data[name] is not None

        parts = name.split(".")
        inner = data

        for part in parts[:-1]:
            if not isinstance(inner, dict) or part not in inner:
                return False
            inner = inner[part]

        last = parts[-1]
        return isinstance(inner, dict) and last in inner and inner[last] is not None

    def _read_prop(self, name: str, data: Any) -> Any:
        """Read a nested property value using dot-notation.

        Args:
            name: Dot-notation property path, e.g. ``'address.city'``.
            data: The dict (or dict-like object) to read from.

        Returns:
            The value at the given path, or ``None`` if any segment of the path
            is absent.
        """
        if "." not in name:
            if isinstance(data, dict):
                return data.get(name)
            return getattr(data, name, None)

        parts = name.split(".")
        inner = data

        for part in parts[:-1]:
            if not isinstance(inner, dict) or part not in inner:
                return None
            inner = inner[part]

        if not isinstance(inner, dict):
            return None

        last = parts[-1]
        return inner.get(last)

    def _write_prop(self, out: Any, name: str, value: Any) -> None:
        """Write *value* to a nested dict using dot-notation.

        Intermediate dicts are created automatically if they do not already
        exist.

        Args:
            out:   The target dict to write into.
            name:  Dot-notation property path, e.g. ``'address.city'``.
            value: The value to write at the given path.

        Raises:
            ValueError: If a non-dict node is encountered where a dict is
                expected (i.e. two fields share a name prefix, such as
                ``'name'`` and ``'name.first'``).
            ValueError: If the leaf key already exists (duplicate field
                detection).
        """
        if "." not in name:
            out[name] = value
            return

        parts = name.split(".")
        inner = out

        for part in parts[:-1]:
            if part not in inner:
                inner[part] = {}
            elif not isinstance(inner[part], dict):
                raise ValueError(
                    f"A property with the name `{name}` already exists. "
                    "This can occur if you have properties which share a prefix "
                    "– for example `name` and `name.first`."
                )
            inner = inner[part]

        last = parts[-1]

        if last in inner:
            raise ValueError(f"Duplicate field detected – a field with the name `{name}` already exists.")

        inner[last] = value
