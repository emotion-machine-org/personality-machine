"""Prompt template rendering for v3 LLM calls.

All prompts are stored as Jinja2 markdown templates under the v3/ subdirectory.
Use render_prompt() to load and render them with variables.
"""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

_PROMPTS_DIR = Path(__file__).parent
_env = Environment(
    loader=FileSystemLoader(_PROMPTS_DIR),
    autoescape=False,
    keep_trailing_newline=True,
    undefined=StrictUndefined,
)


def render_prompt(template_path: str, **kwargs: object) -> str:
    """Render a prompt template with Jinja2.

    Args:
        template_path: Relative path from prompts dir (e.g. "v3/task_matching.md")
        **kwargs: Template variables

    Returns:
        Rendered prompt string
    """
    template = _env.get_template(template_path)
    return template.render(**kwargs)


def load_prompt(template_path: str) -> str:
    """Load a prompt template without rendering (for static templates).

    Args:
        template_path: Relative path from prompts dir

    Returns:
        Raw template content
    """
    source = (_PROMPTS_DIR / template_path).read_text()
    return source
