"""Declarative layout DSL — YAML owns arrangement, Python owns drawing."""

from generators.layout_dsl.context import Region, RenderContext
from generators.layout_dsl.engine import render_blocks, render_body
from generators.layout_dsl.field_providers import apply_field_providers, field_provider
from generators.layout_dsl.providers import row_provider
from generators.layout_dsl.schema import validate_body, validate_layout

__all__ = [
    "Region",
    "RenderContext",
    "apply_field_providers",
    "field_provider",
    "render_blocks",
    "render_body",
    "row_provider",
    "validate_body",
    "validate_layout",
]
