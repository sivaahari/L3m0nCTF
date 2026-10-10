"""The HTML fragments (the scoreboard's rows, the programme grid): Jinja templates of this plugin, with autoescaping on for every one.

A name a player chose is shown as text, whatever it holds. The templates are read from this plugin's own folder, so a theme cannot change
or shadow them, and the environment is made once.
"""
import os

from jinja2 import Environment, FileSystemLoader

_env = Environment(
    loader=FileSystemLoader(os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates", "l3mon_board")),
    autoescape=True,  # for every template, whatever its extension
    keep_trailing_newline=False,
)


def render(name: str, **context) -> str:
    return _env.get_template(name).render(**context)
