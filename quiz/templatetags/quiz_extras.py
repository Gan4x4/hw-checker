from __future__ import annotations

from django import template
from ..utils import wrap_text_html

register = template.Library()


@register.inclusion_tag("admin/quiz/filter.html")
def compact_admin_filter(cl, spec):
    return {"title": spec.title, "choices": list(spec.choices(cl))}


@register.filter(name="wrap_long_lines")
def wrap_long_lines(value: str | None, width: int | str | None = None) -> str:
    """Return HTML with ``<br>`` between wrapped lines of ``value``."""

    return wrap_text_html(value, width=width)
