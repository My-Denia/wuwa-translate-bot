"""HTML for the owner-private web presentation layer.

Rendered from Python rather than from a template engine, because adding one
would add a runtime dependency to a process whose dependency set is currently
httpx + fastapi + uvicorn, and this surface is two views. The trade is that
escaping is this module's responsibility instead of a framework's, so there is
exactly one rule here and it has no exceptions: EVERY value that did not
originate in this file goes through ``esc``. Nothing is interpolated raw, and
no caller-supplied value is ever placed anywhere but element text or a quoted
attribute value.

The page is also entirely self-contained - no external stylesheet, no font
download, no script from anywhere. A phone browser on a slow mobile connection
renders it in one round trip, and there is no third-party origin involved in a
surface whose whole point is that it is private.
"""

from __future__ import annotations

from html import escape as _escape

from wuwaterm.application import (
    DIRECTION_TO_CHINESE,
    DIRECTION_TO_ENGLISH,
    KIND_EXACT,
    KIND_FUZZY,
    KIND_LLM,
)

# The pipeline's OWN vocabulary, imported. An earlier revision of this file
# invented two of its own - it tested `kind == "dictionary"` when the pipeline
# emits "exact"/"fuzzy", and mapped directions "zh2en"/"en2zh" when the
# pipeline emits "zh"/"en". Neither invented key ever matched, so every answer
# was labelled as coming from the model and the direction was printed raw. On a
# product whose promise is "official dictionary first, model only on a miss",
# labelling a dictionary hit as a model translation is not a cosmetic bug: it
# misreports provenance, which is the one thing the label exists to carry.
_SOURCE_LABELS = {
    KIND_EXACT: "词典 · 官方译名",
    KIND_FUZZY: "词典 · 近似匹配",
    KIND_LLM: "模型",
}
_DIRECTION_LABELS = {
    DIRECTION_TO_CHINESE: "译为中文",
    DIRECTION_TO_ENGLISH: "译为英文",
}


def esc(value: object) -> str:
    """Escape a value for HTML text or a quoted attribute.

    ``quote=True`` matters: it escapes the double quote as well, which is what
    makes interpolation into ``attr="..."`` safe. The default would not.
    """
    return _escape("" if value is None else str(value), quote=True)


# Type sizing is set for reading Chinese on a phone held in one hand.
#
# 16px on the inputs is not an aesthetic choice: iOS Safari zooms the viewport
# when a focused input's font-size is below 16px, which throws the layout off
# centre on the first tap into the search box and cannot be undone by the page.
#
# line-height is 1.75 rather than the ~1.5 that reads well for Latin text.
# Chinese glyphs occupy the full em box with no x-height relief, so lines set
# tighter than about 1.7 visually merge; the value is set on the body so that
# every block inherits it.
_STYLE = """
:root {
  color-scheme: light dark;
  --bg: #f6f7f9;
  --panel: #ffffff;
  --ink: #1a1c1f;
  --muted: #5d6570;
  --line: #dfe3e8;
  --accent: #2f6fdb;
  --accent-ink: #ffffff;
  --warn-bg: #fdf2f2;
  --warn-line: #e6b3b3;
  --warn-ink: #8a2020;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #14161a;
    --panel: #1d2026;
    --ink: #e8eaed;
    --muted: #9aa3af;
    --line: #2e333c;
    --accent: #5b93f0;
    --accent-ink: #0d1117;
    --warn-bg: #2a1c1c;
    --warn-line: #6b3535;
    --warn-ink: #f0b4b4;
  }
}
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body {
  background: var(--bg);
  color: var(--ink);
  font-family: -apple-system, BlinkMacSystemFont, "PingFang SC",
    "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans CJK SC",
    "Source Han Sans SC", sans-serif;
  font-size: 16px;
  line-height: 1.75;
  -webkit-text-size-adjust: 100%;
}
.wrap { max-width: 40rem; margin: 0 auto; padding: 1rem 1rem 3rem; }
header { padding: 0.5rem 0 1rem; }
h1 { font-size: 1.25rem; margin: 0; letter-spacing: 0.02em; }
header p { margin: 0.25rem 0 0; color: var(--muted); font-size: 0.875rem; }
nav { display: flex; gap: 0.5rem; margin-bottom: 1rem; }
nav a {
  flex: 1 1 0;
  text-align: center;
  padding: 0.7rem 0.5rem;
  border: 1px solid var(--line);
  border-radius: 0.6rem;
  background: var(--panel);
  color: var(--muted);
  text-decoration: none;
  font-size: 0.95rem;
}
nav a[aria-current="page"] {
  background: var(--accent);
  border-color: var(--accent);
  color: var(--accent-ink);
}
form { margin: 0 0 1rem; }
label { display: block; font-size: 0.9rem; color: var(--muted); margin-bottom: 0.4rem; }
input[type="text"], textarea {
  width: 100%;
  font: inherit;
  font-size: 16px;
  color: var(--ink);
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 0.6rem;
  padding: 0.7rem 0.8rem;
}
textarea { min-height: 7rem; resize: vertical; line-height: 1.75; }
button {
  margin-top: 0.75rem;
  width: 100%;
  /* 44px is the smallest reliably tappable target on a phone. */
  min-height: 2.75rem;
  font: inherit;
  font-size: 1rem;
  color: var(--accent-ink);
  background: var(--accent);
  border: 0;
  border-radius: 0.6rem;
}
.card {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 0.6rem;
  padding: 0.9rem 1rem;
  margin-bottom: 0.75rem;
}
.card h2 { font-size: 0.8rem; color: var(--muted); margin: 0 0 0.5rem; font-weight: 600; }
/* Chinese has no inter-word spaces, so the default break-on-space rule leaves a
   long run of Han characters overflowing its container on a narrow screen.
   anywhere lets the line break between glyphs, which is how Chinese wraps. */
/* pre-wrap, because the shared pipeline joins translated chunks with newlines
   and the browser's default collapsing turned dialogue, paragraphs and chunked
   results into one continuous block - discarding structure the translation had
   actually preserved. */
.result { margin: 0; overflow-wrap: anywhere; white-space: pre-wrap; }
.term { display: flex; flex-wrap: wrap; gap: 0.35rem 0.75rem; padding: 0.5rem 0; border-top: 1px solid var(--line); overflow-wrap: anywhere; }
.term:first-of-type { border-top: 0; }
.term .zh { font-weight: 600; }
.term .en { color: var(--accent); overflow-wrap: anywhere; }
.term .meta { color: var(--muted); font-size: 0.85rem; width: 100%; }
.empty { color: var(--muted); margin: 0; }
.error {
  background: var(--warn-bg);
  border: 1px solid var(--warn-line);
  color: var(--warn-ink);
  border-radius: 0.6rem;
  padding: 0.8rem 1rem;
  margin-bottom: 0.75rem;
  overflow-wrap: anywhere;
}
footer { margin-top: 1.5rem; color: var(--muted); font-size: 0.8rem; }
"""

_TAB_LOOKUP = "lookup"
_TAB_TRANSLATE = "translate"


def _nav(mount: str, active: str) -> str:
    items = ((_TAB_LOOKUP, "查词"), (_TAB_TRANSLATE, "翻译"))
    parts = []
    for key, label in items:
        current = ' aria-current="page"' if key == active else ""
        href = f"{mount}/" if key == _TAB_LOOKUP else f"{mount}/{key}"
        parts.append(f'<a href="{esc(href)}"{current}>{esc(label)}</a>')
    return "<nav>" + "".join(parts) + "</nav>"


def page(*, mount: str, active: str, body: str) -> str:
    """The full document. ``body`` is already-rendered, already-escaped HTML."""
    title = "鸣潮术语" if active == _TAB_LOOKUP else "鸣潮翻译"
    return (
        "<!doctype html>\n"
        '<html lang="zh-CN">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        # width=device-width is what makes the phone lay the page out at its own
        # width instead of at a desktop default and then shrinking it.
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        # This surface is private. Telling well-behaved crawlers not to index it
        # is not a security control - the edge is - but it costs one line.
        '<meta name="robots" content="noindex, nofollow">\n'
        f"<title>{esc(title)}</title>\n"
        f"<style>{_STYLE}</style>\n"
        "</head>\n"
        "<body>\n"
        '<div class="wrap">\n'
        "<header>\n"
        f"<h1>{esc(title)}</h1>\n"
        "<p>私用工具，仅限本人使用。</p>\n"
        "</header>\n"
        f"{_nav(mount, active)}\n"
        f"{body}\n"
        "<footer>词条以官方译名为准；句子翻译在词典未命中时才会调用模型。</footer>\n"
        "</div>\n"
        "</body>\n"
        "</html>\n"
    )


def error_block(message: str) -> str:
    return f'<div class="error" role="alert">{esc(message)}</div>'


def lookup_view(*, mount: str, query: str = "", matches=None, searched: bool = False) -> str:
    form = (
        f'<form method="post" action="{esc(mount)}/lookup">\n'
        '<label for="q">查询词条</label>\n'
        '<input type="text" id="q" name="q" inputmode="search" '
        'autocomplete="off" autocapitalize="off" spellcheck="false" '
        f'placeholder="输入中文或英文词条" value="{esc(query)}">\n'
        "<button type=\"submit\">查词</button>\n"
        "</form>\n"
    )
    if not searched:
        return form
    rows = []
    for match in matches or ():
        meta_bits = []
        if getattr(match, "category", None):
            meta_bits.append(str(match.category))
        if getattr(match, "reason", None):
            meta_bits.append(str(match.reason))
        meta = f'<span class="meta">{esc(" · ".join(meta_bits))}</span>' if meta_bits else ""
        rows.append(
            '<div class="term">'
            f'<span class="zh">{esc(match.zh)}</span>'
            f'<span class="en">{esc(match.en)}</span>'
            f"{meta}"
            "</div>"
        )
    if rows:
        body = f'<div class="card"><h2>词典结果</h2>{"".join(rows)}</div>'
    else:
        body = (
            '<div class="card"><h2>词典结果</h2>'
            '<p class="empty">词典中没有这个词条。可以到「翻译」里让模型处理整句。</p>'
            "</div>"
        )
    return form + body


def translate_view(
    *, mount: str, text: str = "", result=None, translated: bool = False
) -> str:
    form = (
        f'<form method="post" action="{esc(mount)}/translate">\n'
        '<label for="text">待翻译文本</label>\n'
        f'<textarea id="text" name="text" placeholder="粘贴或输入一句话">{esc(text)}</textarea>\n'
        "<button type=\"submit\">翻译</button>\n"
        "</form>\n"
    )
    if not translated or result is None:
        return form
    kind = str(getattr(result, "kind", ""))
    raw_direction = str(getattr(result, "direction", ""))
    # An unrecognised kind claims NOTHING about provenance rather than
    # defaulting to one of the two answers. Guessing here is how the previous
    # revision came to label every dictionary hit as a model translation.
    source = _SOURCE_LABELS.get(kind, kind)
    direction = _DIRECTION_LABELS.get(raw_direction, raw_direction)
    heading = " · ".join(part for part in (source, direction) if part)
    body = (
        '<div class="card">'
        f'<h2>{esc(heading)}</h2>'
        f'<p class="result">{esc(getattr(result, "text", ""))}</p>'
        "</div>"
    )
    return form + body
