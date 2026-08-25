"""Field binding for the layout DSL.

Deliberately minimal: `{FIELD}` substitution and presence tests, nothing else.
No expressions, no arithmetic, no filters — everything a layout references must
be statically checkable before a single pixel is drawn.
"""

import re

_PLACEHOLDER = re.compile(r"\{([A-Z][A-Z0-9_]*)\}")

_ABSENT = "NOT_FOUND"


class BindingError(RuntimeError):
    """Raised when a layout references a field the entry does not carry."""


def referenced_fields(template: str) -> list[str]:
    """Return every field name referenced by a template string, in order.

    Args:
        template: A string that may contain `{FIELD}` placeholders.

    Returns:
        Field names, in order of first appearance, without duplicates removed.
    """
    return _PLACEHOLDER.findall(template)


def interpolate(template: str, fields: dict) -> str:
    """Substitute `{FIELD}` placeholders with the entry's values.

    A field whose value is the corpus-wide `NOT_FOUND` sentinel renders as the
    empty string, matching how the existing renderers suppress absent values.

    Args:
        template: String containing zero or more `{FIELD}` placeholders.
        fields: The entry's `fields` mapping.

    Returns:
        The template with every placeholder replaced.

    Raises:
        BindingError: If a referenced field is absent from `fields`.
    """

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in fields:
            msg = (
                f"Layout references unknown field '{name}'.\n"
                f"  Template: {template!r}\n"
                f"  Available: {sorted(fields)}\n"
                f"  Remediation: fix the field name in the layout, or add "
                f"'{name}' to the entry in ground_truth/."
            )
            raise BindingError(msg)
        value = str(fields[name])
        return "" if value == _ABSENT else value

    return _PLACEHOLDER.sub(replace, template)


def is_present(fields: dict, field: str) -> bool:
    """Report whether a field carries a real value.

    Args:
        fields: The entry's `fields` mapping.
        field: The field name to test.

    Returns:
        False if the field is missing, empty, or the `NOT_FOUND` sentinel.
    """
    value = fields.get(field)
    return value is not None and str(value) not in ("", _ABSENT)
