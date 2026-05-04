"""Copyright 2026 Google LLC

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

     https://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.

Jinja2 wrapper for rendering the JobSet template.

Strict mode: undefined variables raise. We'd rather fail fast at render
time with a clear error than have kubectl reject silently-empty fields.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import jinja2


class TemplateError(Exception):
  """Render-time failure (template missing, undefined variable, etc.)."""


def render(template_path: Path, context: dict[str, Any]) -> str:
  if not template_path.is_file():
    raise TemplateError(f"template not found: {template_path}")

  env = jinja2.Environment(
      loader=jinja2.FileSystemLoader(template_path.parent),
      undefined=jinja2.StrictUndefined,
      keep_trailing_newline=True,
      autoescape=False,            # YAML, not HTML
  )
  try:
    tpl = env.get_template(template_path.name)
    return tpl.render(**context)
  except jinja2.UndefinedError as exc:
    raise TemplateError(
        f"{template_path}: missing template variable — {exc.message}"
    ) from exc
  except jinja2.TemplateSyntaxError as exc:
    raise TemplateError(
        f"{template_path}:{exc.lineno}: {exc.message}"
    ) from exc
