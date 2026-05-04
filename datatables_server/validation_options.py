"""
Validation options – per-validator configuration with fluent chaining.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, List, Optional, Union

if TYPE_CHECKING:
    from .validation_host import ValidationHost

# Type alias for the depends-on callback function.
DependsOnFunc = Callable[[Any, Any, "ValidationHost"], bool]


class ValidationOptions:
    """Common validation options shared by all built-in validators.

    This class acts as both a configuration object and as the base class for
    :class:`~datatables_server.nested_data.NestedData` so that the
    :meth:`run_depends` method can use ``_read_prop`` to navigate nested
    submitted data.

    Instances are typically created by the validators themselves and optionally
    overridden by callers using the fluent chaining API::

        opts = (
            ValidationOptions(message='Please enter a valid email')
            .empty(False)
            .optional(False)
        )
        field.validator(Validate.email(opts))

    The :meth:`depends_on` method can be used to make a validator conditional::

        # Only validate if 'type' field equals 'business'
        opts = ValidationOptions().depends_on('type', 'business')
    """

    def __init__(
        self,
        message: str = "Input not valid",
        empty: bool = True,
        optional: bool = True,
    ) -> None:
        """Create a ``ValidationOptions`` instance with sensible defaults.

        Args:
            message:  Error message returned when validation fails.
            empty:    When ``True`` (the default), empty strings (``''``) are
                      considered valid and bypass validators.  Set to ``False``
                      to require a non-empty value.
            optional: When ``True`` (the default), the field does not need to be
                      present in the submitted data.  Set to ``False`` to make
                      the field mandatory.
        """
        self._message: str = message
        self._empty: bool = empty
        self._optional: bool = optional

        # Depends-on state
        self._depends_field: Optional[str] = None
        self._depends_value: Any = None
        self._depends_fn: Optional[DependsOnFunc] = None

    # ------------------------------------------------------------------
    # depends_on
    # ------------------------------------------------------------------

    def depends_on(
        self,
        field_or_fn: Union[str, DependsOnFunc],
        value: Any = None,
    ) -> "ValidationOptions":
        """Conditionally apply this validator based on another field's value.

        This method can be called in three ways:

        1. **Callable**: ``depends_on(fn)`` – the validator runs only when
           ``fn(val, data, host)`` returns ``True``.
        2. **Field name only**: ``depends_on('field_name')`` – the validator
           runs only when the named field has a non-empty, non-``None`` value
           in the submitted data.
        3. **Field name + value(s)**: ``depends_on('field_name', 'value')`` or
           ``depends_on('field_name', ['v1', 'v2'])`` – the validator runs only
           when the named field matches one of the given value(s).

        Args:
            field_or_fn: Either a callable ``(val, data, host) -> bool`` or a
                         string field name.
            value:       When *field_or_fn* is a field name, the value (or list
                         of values) to match against.  Omit to match any
                         non-empty value.

        Returns:
            ``self``, enabling fluent chaining.
        """
        if callable(field_or_fn):
            self._depends_fn = field_or_fn
            self._depends_field = None
            self._depends_value = None
        else:
            self._depends_fn = None
            self._depends_field = field_or_fn
            self._depends_value = value

        return self

    # ------------------------------------------------------------------
    # empty
    # ------------------------------------------------------------------

    def empty(self, set: Optional[bool] = None) -> Union["ValidationOptions", bool]:
        """Get or set whether empty strings are considered valid.

        When called with no argument (or ``None``), returns the current
        boolean value.  When called with a :class:`bool`, sets the value and
        returns ``self`` for chaining.

        Args:
            set: ``True`` to allow empty strings (default), ``False`` to
                 require a non-empty value.  Pass ``None`` to use as a getter.

        Returns:
            Current value (bool) when used as a getter, or ``self`` when used
            as a setter.
        """
        if set is None:
            return self._empty

        self._empty = set
        return self

    # ------------------------------------------------------------------
    # message
    # ------------------------------------------------------------------

    def message(self, msg: Optional[str] = None) -> Union["ValidationOptions", str]:
        """Get or set the validation error message.

        Args:
            msg: New error message string.  Pass ``None`` to use as a getter.

        Returns:
            Current message (str) when used as a getter, or ``self`` when used
            as a setter.
        """
        if msg is None:
            return self._message

        self._message = msg
        return self

    # ------------------------------------------------------------------
    # optional
    # ------------------------------------------------------------------

    def optional(self, set: Optional[bool] = None) -> Union["ValidationOptions", bool]:
        """Get or set whether the field is optional in the submitted data.

        When ``True`` (default) the field is not required to be present in the
        submitted data.  When ``False`` the field *must* be submitted.

        Args:
            set: ``True`` to make the field optional (default), ``False`` to
                 require it to be submitted.  Pass ``None`` to use as a getter.

        Returns:
            Current value (bool) when used as a getter, or ``self`` when used
            as a setter.
        """
        if set is None:
            return self._optional

        self._optional = set
        return self

    # ------------------------------------------------------------------
    # run_depends
    # ------------------------------------------------------------------

    def run_depends(self, val: Any, data: Any, host: "ValidationHost") -> bool:
        """Determine whether this validator should run for the current submission.

        Called internally by the validator pipeline before invoking the actual
        validation logic.

        Args:
            val:  The submitted value for the field being validated.
            data: The full submitted data dict for the current row.
            host: The :class:`~datatables_server.ValidationHost` containing
                  context such as the Editor instance and database connection.

        Returns:
            ``True`` if the validator should be applied, ``False`` to skip it.
        """
        if self._depends_fn is not None:
            # Delegate entirely to the user-supplied function.
            return self._depends_fn(val, data, host)

        if self._depends_field is not None:
            # Read the dependent field's value from the submitted data.
            dep_val = self._read_nested(self._depends_field, data)

            if self._depends_value is not None:
                # Match against one or more specific values.
                if isinstance(self._depends_value, list):
                    return dep_val in self._depends_value
                return dep_val == self._depends_value

            # No target value – just check that the dependent field is non-empty.
            return dep_val is not None and dep_val != ""

        # No depends condition – always run the validator.
        return True

    # ------------------------------------------------------------------
    # select (class method)
    # ------------------------------------------------------------------

    @staticmethod
    def select(user: Optional["ValidationOptions"]) -> "ValidationOptions":
        """Return *user* if provided, otherwise return a new default instance.

        This is a convenience helper used by built-in validators so that callers
        can optionally supply their own :class:`ValidationOptions` object without
        needing to explicitly create a default one.

        Args:
            user: A caller-supplied :class:`ValidationOptions` instance, or
                  ``None``.

        Returns:
            *user* if not ``None``, otherwise a freshly constructed
            :class:`ValidationOptions` with default settings.
        """
        return user if user is not None else ValidationOptions()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _read_nested(self, name: str, data: Any) -> Any:
        """Read a possibly dot-notated property from *data*.

        This mirrors the behaviour of
        :meth:`~datatables_server.NestedData._read_prop` without requiring
        :class:`ValidationOptions` to inherit from
        :class:`~datatables_server.NestedData`.

        Args:
            name: Dot-notation property path.
            data: Source dict.

        Returns:
            The value at the path, or ``None`` if any segment is absent.
        """
        if not isinstance(data, dict):
            return None

        if "." not in name:
            return data.get(name)

        parts = name.split(".")
        inner: Any = data

        for part in parts[:-1]:
            if not isinstance(inner, dict) or part not in inner:
                return None
            inner = inner[part]

        if not isinstance(inner, dict):
            return None

        return inner.get(parts[-1])
