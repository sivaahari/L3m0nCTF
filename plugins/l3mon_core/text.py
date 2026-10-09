"""The plain-text rules the plugins share.

Two rules, because there are two kinds of reader:

- `UNSAFE_TEXT` / `plain_text`: the strict rule, for words that CTFd itself renders as markdown, a link or a template, such as the
  name of a channel inside the "New on air" notification. It refuses (or removes) every character that could turn text into
  something else.
- `crew_text`: the rule for a sentence the crew writes for a studio or the audit trail (a void reason, a bonus message). Those are
  stored in our own tables and shown by our own pages as text, so apostrophes, underscores, brackets and slashes are fine; what is
  refused is what could be taken for markup (`<` and `>`) and anything that is not a printable sentence (control characters).
"""
import re

# Characters that would make text something other than text when a notification or a page renders it as markup, a link or a
# template ({ctf_name}). The plan refuses them in the words players read (a channel's name, its storyline, a sponsor's name), and
# the announcement drops them again in case a row got there some other way.
UNSAFE_TEXT = re.compile(r"[<>\[\]{}*_`\\#|~\x00-\x1f]|//")
UNSAFE_MESSAGE = "may not contain < > [ ] { } * _ ` \\ # | ~ or //"

# Control characters (C0 and C1), the line and paragraph separators, the zero-width space, the word joiner and invisible operators, the
# soft hyphen, the byte-order mark, the Arabic letter mark, the tag characters and the marks that change the direction of text: they can
# reorder or hide what a page shows. The zero-width joiner and non-joiner are allowed: real scripts
# (Malayalam, Kannada, Tamil, Hindi) and emoji sequences need them.
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u200b\u200e\u200f\u2028\u2029\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff\xad\u061c\U000e0000-\U000e007f]")


def plain_text(value: str) -> str:
    """The text with every character that could turn it into markup taken out."""
    return UNSAFE_TEXT.sub("", value)


def crew_text(value, limit, required=True):
    """-> (clean, problem). `clean` is the trimmed sentence, or None when there is a problem (a short message that says what to
    change). An empty text is a problem only when `required`; then it is stored as an empty string."""
    if value is None and not required:
        return "", None
    if not isinstance(value, str):
        return None, "must be text"
    clean = value.strip()
    if not clean:
        return (None, "may not be empty") if required else ("", None)
    if len(clean) > limit:
        return None, f"may be at most {limit} characters"
    if _CONTROL.search(clean):
        return None, "may not contain control characters (tabs, line breaks, invisible or direction-changing marks)"
    if "<" in clean or ">" in clean:
        return None, "may not contain < or >"
    return clean, None
