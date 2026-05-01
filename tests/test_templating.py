"""Tests for the Jinja2 template renderer."""

from __future__ import annotations

from pathlib import Path

import pytest

from cde import templating


def _tpl(p: Path, body: str) -> Path:
  p.write_text(body, encoding="utf-8")
  return p


def test_renders_basic(tmp_path):
  p = _tpl(tmp_path / "t.j2", "name: {{ name }}\nimage: {{ image }}\n")
  out = templating.render(p, {"name": "v140", "image": "gcr.io/x:cde-abc"})
  assert "name: v140" in out
  assert "image: gcr.io/x:cde-abc" in out


def test_renders_iter(tmp_path):
  p = _tpl(
      tmp_path / "t.j2",
      "args:\n{% for k, v in overrides.items() %}  - {{ k }}={{ v }}\n{% endfor %}",
  )
  out = templating.render(p, {"overrides": {"ep": 32, "fsdp": 16}})
  assert "  - ep=32" in out
  assert "  - fsdp=16" in out


def test_undefined_variable_raises_clean(tmp_path):
  p = _tpl(tmp_path / "t.j2", "{{ does_not_exist }}\n")
  with pytest.raises(templating.TemplateError, match="missing template variable"):
    templating.render(p, {})


def test_missing_template_file(tmp_path):
  with pytest.raises(templating.TemplateError, match="template not found"):
    templating.render(tmp_path / "no-such.j2", {})


def test_syntax_error_raises_clean(tmp_path):
  p = _tpl(tmp_path / "t.j2", "{% if foo %}\n")
  with pytest.raises(templating.TemplateError):
    templating.render(p, {"foo": True})
