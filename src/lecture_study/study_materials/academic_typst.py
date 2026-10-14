"""Shared ABNT-aligned Typst presentation rules for study documents."""

from __future__ import annotations

import re
from typing import Iterable
from unicodedata import category

_MAX_UNBREAKABLE_WARNING_CHARACTERS = 480
_MAX_UNBREAKABLE_WARNING_ITEMS = 4


def academic_document_start(
    *,
    title: str,
    subtitle: str,
    source_note: str | None = None,
) -> list[str]:
    """Return page, typography, title-page, and pagination declarations."""

    escaped_title = typst_string(title)
    escaped_subtitle = typst_string(subtitle)
    lines = [
        '#set page(paper: "a4", margin: (top: 30mm, bottom: 20mm, '
        "left: 30mm, right: 20mm))",
        '#set text(font: "Arial", lang: "pt", size: 12pt, hyphenate: false, '
        'fill: rgb("1a1a1a"))',
        # ABNT NBR 14724:2024 calls for 1.5 line spacing; Typst's `leading`
        # is the gap ADDED between lines (not a multiplier), and the
        # previous 0.5em value was tighter than Typst's own 0.65em single-
        # spacing default -- the exact opposite of what was intended,
        # producing the cramped "linhas todas juntas" look. 1em leading
        # plus 12pt body text approximates ABNT 1.5 spacing.
        #
        # Paragraph spacing (2026-09-15, operator feedback): went through
        # 0pt (strict ABNT, indent-only -- read as paragraphs touching on
        # screen) and 4pt (still visually indistinguishable from a
        # same-paragraph line break) before the operator asked directly
        # for a full blank-line-equivalent gap between paragraphs,
        # unambiguous even at a glance, over staying closer to a print
        # ABNT convention.
        "#set par(justify: true, leading: 1em, spacing: 18pt, "
        "first-line-indent: (amount: 1.25cm, all: true), "
        'linebreaks: "optimized")',
        '#set heading(numbering: "1.1")',
        # A single sober accent color (a muted academic blue) distinguishes
        # heading levels from the black body text without turning the
        # document colorful -- the operator asked for "sóbrio, mas com
        # alguma cor".
        # 2026-09-15 document review, round 2: the first attempt at
        # separating heading levels still left H3 at 11pt -- SMALLER than
        # the 12pt body text -- so a unit's own subsection headings
        # ("Topografia e mobilidade", "Irrigação e relação com o
        # estômago") were nearly invisible, read as barely-distinguishable
        # italic body text rather than headings at all. Every level is now
        # unambiguously larger than body text (12pt), not just larger
        # relative to each other, with more vertical air above each to
        # separate it from the paragraph before it.
        # above/below roughly doubled again in round 4 (operator: "quero
        # mais espaço nos títulos") -- a heading needs to read as a clear
        # break from the text before it, not just a slightly bigger gap.
        "#show heading.where(level: 1): it => block(width: 100%, above: 32pt, "
        "below: 18pt)[\n"
        "#set par(first-line-indent: 0pt, justify: false)\n"
        '#text(size: 16pt, weight: "bold", fill: rgb("1f3a5f"))[#it.body]\n]',
        "#show heading.where(level: 2): it => block(width: 100%, above: 26pt, "
        "below: 14pt)[\n"
        "#set par(first-line-indent: 0pt, justify: false)\n"
        '#text(size: 14pt, weight: "bold", fill: rgb("1f3a5f"))[#it.body]\n]',
        # Level 3 previously had no show rule at all, so a unit's internal
        # subsections (e.g. Codex drafting's own H3 topic breaks) rendered
        # as indistinguishable indented body text instead of a visible
        # subheading.
        "#show heading.where(level: 3): it => block(width: 100%, above: 22pt, "
        "below: 10pt)[\n"
        "#set par(first-line-indent: 0pt, justify: false)\n"
        '#text(size: 13pt, weight: "bold", style: "italic", '
        'fill: rgb("3d5a80"))[#it.body]\n]',
        "#align(center + horizon)[",
        "#block(width: 100%)[",
        "#set par(first-line-indent: 0pt, justify: false)",
        f'#text(size: 18pt, weight: "bold")[#text({escaped_title})]',
        "#v(10pt)",
        # A thin accent rule in the same heading blue as the rest of the
        # document (2026-09-15 document review: the cover page was bare
        # centered text with no visual identity at all).
        '#box(width: 40mm, height: 1.4pt, fill: rgb("1f3a5f"))',
        "#v(10pt)",
        f'#text(size: 14pt, weight: "bold")[#text({escaped_subtitle})]',
    ]
    if source_note:
        lines.extend(
            (
                "#v(24pt)",
                f"#text(size: 10pt)[#text({typst_string(source_note)})]",
            )
        )
    lines.extend(
        (
            "]",
            "]",
            "#pagebreak()",
            "#set page(header: context [#align(right)[#text(size: 10pt)"
            '[#counter(page).display("1")]]])',
        )
    )
    return lines


def render_academic_body(markdown: str) -> list[str]:
    """Render simple Markdown paragraphs, bullets, and subheadings (### and
    deeper -- the compiler already owns levels 1-2 for chapter/section
    titles, see render_study_edition_typst) as semantic Typst blocks.

    A ``### Subtopic`` line inside body_markdown used to have its marker
    stripped and fall through to a plain paragraph, so a drafting stage
    that organizes a long unit into subsections (as the Codex drafting
    prompt now asks for) rendered as undifferentiated body text -- no
    visible subheading at all in the PDF."""

    rendered: list[str] = []
    paragraph_lines: list[str] = []
    for level, line in _normalized_markdown_lines(markdown):
        if not line:
            _flush_paragraph(paragraph_lines, rendered)
            continue
        if level > 0:
            _flush_paragraph(paragraph_lines, rendered)
            rendered.append(f"#heading(level: {level})[#text({typst_string(line)})]")
            continue
        if line.startswith("- "):
            _flush_paragraph(paragraph_lines, rendered)
            rendered.append(render_academic_list_item(line.removeprefix("- ")))
            continue
        paragraph_lines.append(line)
    _flush_paragraph(paragraph_lines, rendered)
    return rendered


def render_academic_figure(relative_path: str, caption: str) -> str:
    """Render one captioned lecture frame as a Typst figure block, sized
    to stay well within the page margins and kept together with its
    caption across a page break (`breakable: false`).

    Spacing is explicit (2026-09-15, operator feedback: figure/caption
    spacing "está errado") rather than left to Typst's own #figure
    defaults: `above`/`below` on the wrapping block give it the same
    rhythm as a heading rather than colliding with the paragraph above
    or the text right after it, `gap` sets a deliberate small breathing
    space between the image and its caption, and the caption is styled
    smaller/italic/muted so it reads as a caption, not as another line
    of body prose."""

    return (
        "#block(width: 100%, above: 12pt, below: 14pt, breakable: false)[\n"
        "#set par(first-line-indent: 0pt, justify: false)\n"
        "#figure(\n"
        f"  image({typst_string(relative_path)}, width: 85%),\n"
        "  gap: 8pt,\n"
        '  caption: text(size: 10pt, style: "italic", '
        f'fill: rgb("46564d"))[{render_typst_prose(caption)}],\n'
        ")\n"
        "]"
    )


def render_academic_warning_block(
    warnings: Iterable[str],
) -> str:
    """Render clinical warnings as a restrained academic callout.

    A long warning list is split into several chunks, each its own
    unbreakable block, so Typst never has to fit one oversized block on a
    single page. Only the first chunk carries the "Alertas e pontos de
    atenção" title -- every chunk repeating it read as several distinct,
    unrelated alerts stacked back to back instead of one continued list
    (2026-09-15 document review)."""

    chunks = _warning_callout_chunks(warnings)
    return "\n".join(
        _render_warning_callout(chunk, is_continuation=index > 0)
        for index, chunk in enumerate(chunks)
    )


def _warning_callout_chunks(
    warnings: Iterable[str],
) -> tuple[tuple[str, ...], ...]:
    chunks: list[tuple[str, ...]] = []
    current: list[str] = []
    for warning in warnings:
        if _warning_requires_own_callout(warning):
            if current:
                chunks.append(tuple(current))
                current = []
            chunks.append((warning,))
            continue
        if _warning_fills_callout(current, warning):
            chunks.append(tuple(current))
            current = []
        current.append(warning)
    if current:
        chunks.append(tuple(current))
    return tuple(chunks)


def _warning_requires_own_callout(warning: str) -> bool:
    has_multiple_lines = len(warning.splitlines(keepends=True)) > 1
    return has_multiple_lines or len(warning) > _MAX_UNBREAKABLE_WARNING_CHARACTERS


def _warning_fills_callout(current: list[str], warning: str) -> bool:
    current_characters = sum(len(item) for item in current)
    return bool(current) and (
        len(current) >= _MAX_UNBREAKABLE_WARNING_ITEMS
        or current_characters + len(warning) > _MAX_UNBREAKABLE_WARNING_CHARACTERS
    )


def _render_warning_callout(
    warnings: tuple[str, ...],
    *,
    is_continuation: bool,
) -> str:
    rendered_warnings = "\n".join(
        render_academic_list_item(warning) for warning in warnings
    )
    is_oversized_warning = len(warnings) == 1 and _warning_requires_own_callout(
        warnings[0]
    )
    breakable = "true" if is_oversized_warning else "false"
    title = (
        "Alertas e pontos de atenção (continuação)"
        if is_continuation
        else "Alertas e pontos de atenção"
    )
    return (
        # above/below: 20pt/22pt (2026-09-15 document review, round 4 --
        # "mais espaço antes de começar e ao terminar o quadro"; was
        # 10pt/12pt, barely more than a body paragraph gap for a callout
        # meant to stand apart from the surrounding text). justify: true
        # (was false) -- "quero textos justificados dentro do quadro,
        # tudo alinhadinho": the box's own text read ragged-right while
        # every other block in the document is justified.
        '#block(width: 100%, stroke: rgb("8a1f11"), radius: 2pt, '
        f"inset: 8pt, above: 20pt, below: 22pt, breakable: {breakable})[\n"
        "#set par(first-line-indent: 0pt, justify: true)\n"
        f'#text(weight: "bold", fill: rgb("8a1f11"))[{title}]\n'
        "#v(5pt)\n"
        f"{rendered_warnings}\n"
        "]"
    )


def typst_string(value: str) -> str:
    """Escape untrusted text as a Typst string literal."""

    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def render_typst_prose(value: str) -> str:
    """Render prose while keeping hyphenated letter compounds unbroken."""

    rendered: list[str] = []
    cursor = 0
    for start, end in _hyphenated_letter_compound_spans(value):
        if start > cursor:
            rendered.append(f"#text({typst_string(value[cursor:start])})")
        rendered.append(f"#box[#text({typst_string(value[start:end])})]")
        cursor = end
    if cursor < len(value):
        rendered.append(f"#text({typst_string(value[cursor:])})")
    return "".join(rendered) or f"#text({typst_string(value)})"


def _hyphenated_letter_compound_spans(value: str) -> Iterable[tuple[int, int]]:
    cursor = 0
    while cursor < len(value):
        if not value[cursor].isalpha():
            cursor += 1
            continue
        start = cursor
        cursor = _consume_letter_run(value, cursor)
        hyphen_count = 0
        while (
            cursor + 1 < len(value)
            and value[cursor] == "-"
            and value[cursor + 1].isalpha()
        ):
            hyphen_count += 1
            cursor = _consume_letter_run(value, cursor + 1)
        if hyphen_count:
            yield start, cursor


def _consume_letter_run(value: str, start: int) -> int:
    cursor = start
    while cursor < len(value) and (
        value[cursor].isalpha() or category(value[cursor]).startswith("M")
    ):
        cursor += 1
    return cursor


# Hanging-indent for an option's own wrapped continuation line only
# (round 4) -- not applied to the stem, which has no marker of its own
# to hang under (round 5). See render_academic_list_item's docstring.
_OPTION_HANGING_INDENT = "1.4em"

# Extra vertical space between a stem and its first option only, on top
# of the block's own `spacing: 8pt` between subsequent options (operator
# report, 2026-09-16): distinguishes "this is the answer list starting"
# from "this is the next option" without touching the option-to-option
# rhythm. See render_academic_list_item's docstring.
_STEM_TO_FIRST_OPTION_GAP = "8pt"

# Content-based signal for "this continuation line is a lettered exam
# option" ("A) ...", "B) ..."), not "this is the second line of a
# multi-line item" -- render_academic_list_item() is shared by warning
# callouts and "Pontos essenciais" summaries too (2026-09-16,
# principal-code-reviewer BLOCK finding: a position-only rule leaked the
# stem-to-first-option gap into an arbitrary multi-line warning, which
# has no notion of "options" at all).
_OPTION_LETTER_LINE = re.compile(r"^[A-Z]\)\s")


def render_academic_list_item(item: str, *, marker: str = "-") -> str:
    """Render one list item without paragraph indentation.

    2026-09-15 document review, round 4: an option's own wrapped
    continuation line ("A) O baço é um órgão dinâmico, e seu
    posicionamento\\ndepende do tamanho...") used to land flush with the
    block's own left edge -- identical to where the NEXT option ("B) ...")
    starts -- so a reader could not tell a wrap from a new option at a
    glance. `item` may carry embedded "\\n"-separated lines (a
    multi-option question, built in bundle.py); each such line now
    renders as its own paragraph with its own hanging indent, so only
    that option's own wrap aligns under it. A blank line between two
    lines (bundle.py's paragraph-break convention between an item's
    distinct sections) becomes real vertical space instead of just
    another indented line.

    2026-09-15, round 5: the stem (`lines[0]`) has no per-line #par of
    its own -- it inherits the block's default paragraph settings -- so
    giving it the same hanging-indent as the options made its own
    wrapped line land indented past every option letter below it,
    reading as stem/option misalignment. The stem's own wrap carries no
    marker to hang under (unlike each lettered option), so it gets no
    hanging-indent and lands flush with the option letters instead. Each
    option keeps its hanging-indent so a long option's own wrap still
    stays visually distinct from the next option -- round 4's fix -- and
    round 5's flush-alignment applies to the stem only.

    2026-09-16 (operator report, lecture 10003 exam question 1): a long
    stem wrapping onto a second line inherited the document's global
    ABNT-1.5 `leading: 1em` (academic_document_start()'s own reading-prose
    rule), which is LARGER than this block's own `spacing: 8pt` between
    two distinct paragraphs (e.g. the stem and option A). The result: the
    stem's own same-paragraph wrap looked MORE separated than two
    genuinely distinct options, reading as an accidental paragraph break
    even with a single, already-collapsed-newline-free source string
    (confirmed by compiling this exact block in isolation and comparing
    pixel columns -- the wrap is correctly flush-aligned; only the
    vertical gap is wrong). `leading: 0.65em` here is Typst's own
    documented single-spacing default (see academic_document_start()'s
    comment on why 1em was chosen for ABNT prose) -- tightening only this
    list-rendering function's own same-paragraph wraps, never the
    document's regular reading paragraphs outside it.

    2026-09-16, second operator report on the same fix: once the wrap gap
    above was resolved, the operator asked for the OPPOSITE at the
    stem-to-first-option boundary -- more visible space between the
    question and "A)", not less, distinguishing it from the tighter
    option-to-option gap. `_STEM_TO_FIRST_OPTION_GAP` adds that once, via
    an explicit `#v(...)`, right before the first continuation line that
    actually looks like a lettered option (`_OPTION_LETTER_LINE`) -- a
    plain multi-line warning callout or "Pontos essenciais" summary has
    no such line and gets no extra gap, and a blank line preceding the
    first real option no longer swallows it (principal-code-reviewer
    BLOCK: the first cut gated on line *position* alone, which both
    leaked the gap into non-option multi-line items and dropped it
    silently whenever a blank line came first).
    """

    item = _remove_markdown_emphasis(item)
    lines = item.split("\n")
    # below: 32pt (round 5: operator still read 24pt -- round 3's own
    # raise from 14pt -- as too little space between one question and
    # the next). spacing: 8pt (round 5: 4pt between one option and the
    # next option read as "opções coladas"; this is the gap between two
    # different options in the same block, not the wrap-to-marker gap
    # inside one option). leading: 0.65em (2026-09-16: Typst's own
    # single-spacing default, overriding the document's larger ABNT
    # leading so a wrapped line stays visually tighter than the gap
    # between two distinct paragraphs -- see docstring). This is the
    # single function every bulleted/numbered block in the document
    # (warning callouts, "Pontos essenciais" summaries, exam questions)
    # renders through, so one change here reaches all of them.
    parts = [
        "#block(width: 100%, inset: (left: 1.25cm), below: 32pt)[",
        "#set par(first-line-indent: 0pt, hanging-indent: 0pt, "
        "spacing: 8pt, leading: 0.65em)",
        f"#text({typst_string(f'{marker} ')}){render_typst_prose(lines[0])}",
    ]
    stem_gap_pending = True
    for line in lines[1:]:
        stripped = line.strip()
        if not stripped:
            parts.append("#v(10pt)")
            continue
        if stem_gap_pending and _OPTION_LETTER_LINE.match(stripped):
            parts.append(f"#v({_STEM_TO_FIRST_OPTION_GAP})")
        stem_gap_pending = False
        parts.append(
            f"#par(hanging-indent: {_OPTION_HANGING_INDENT})"
            f"[{render_typst_prose(line)}]"
        )
    parts.append("]")
    return "\n".join(parts)


def _normalized_markdown_lines(markdown: str) -> Iterable[tuple[int, str]]:
    """Yield (heading_level, text) per line: heading_level is 0 for a
    plain/bullet line, or the Markdown heading depth (3 for "###", etc.)
    with its marker stripped from the text."""

    without_emphasis = _remove_markdown_emphasis(markdown)
    for raw_line in without_emphasis.splitlines():
        heading_match = re.match(r"^\s*(#{1,6})\s*(.*)$", raw_line)
        if heading_match:
            yield len(heading_match.group(1)), heading_match.group(2).strip()
            continue
        yield 0, raw_line.strip()


def _remove_markdown_emphasis(value: str) -> str:
    rendered_blocks: list[str] = []
    prose_lines: list[str] = []
    active_fence: tuple[str, int] | None = None
    for line in value.splitlines(keepends=True):
        fence = _markdown_fence(line)
        if active_fence is not None:
            rendered_blocks.append(line)
            if _is_closing_markdown_fence(line, active_fence):
                active_fence = None
            continue
        if fence is not None:
            _flush_markdown_prose(prose_lines, rendered_blocks)
            active_fence = fence
            rendered_blocks.append(line)
            continue
        if not line.strip():
            _flush_markdown_prose(prose_lines, rendered_blocks)
            rendered_blocks.append(line)
            continue
        if line.startswith(("    ", "\t")):
            _flush_markdown_prose(prose_lines, rendered_blocks)
            rendered_blocks.append(line)
            continue
        prose_lines.append(line)
    _flush_markdown_prose(prose_lines, rendered_blocks)
    return "".join(rendered_blocks)


def _flush_markdown_prose(
    prose_lines: list[str],
    rendered_blocks: list[str],
) -> None:
    if not prose_lines:
        return
    rendered_blocks.append(_remove_inline_markdown_emphasis("".join(prose_lines)))
    prose_lines.clear()


def _remove_inline_markdown_emphasis(value: str) -> str:
    rendered: list[str] = []
    cursor = 0
    while cursor < len(value):
        if value[cursor] == "\\" and cursor + 1 < len(value):
            rendered.append(value[cursor : cursor + 2])
            cursor += 2
            continue
        if value[cursor] == "`":
            code_end = _code_span_end(value, cursor)
            if code_end is not None:
                rendered.append(value[cursor:code_end])
                cursor = code_end
                continue
        marker = value[cursor : cursor + 2]
        if marker in {"**", "__"} and _can_open_emphasis(value, cursor, marker):
            closing_index = _closing_emphasis_index(
                value,
                cursor + len(marker),
                marker,
            )
            if closing_index is not None:
                rendered.append(
                    _remove_inline_markdown_emphasis(
                        value[cursor + len(marker) : closing_index]
                    )
                )
                cursor = closing_index + len(marker)
                continue
        rendered.append(value[cursor])
        cursor += 1
    return "".join(rendered)


def _markdown_fence(line: str) -> tuple[str, int] | None:
    match = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
    if match is None:
        return None
    marker = match.group(1)
    return marker[0], len(marker)


def _is_closing_markdown_fence(
    line: str,
    active_fence: tuple[str, int],
) -> bool:
    marker, minimum_length = active_fence
    return (
        re.match(
            rf"^ {{0,3}}{re.escape(marker)}{{{minimum_length},}}\s*$",
            line,
        )
        is not None
    )


def _code_span_end(value: str, start: int) -> int | None:
    run_length = 1
    while start + run_length < len(value) and value[start + run_length] == "`":
        run_length += 1
    cursor = start + run_length
    while cursor < len(value):
        if value[cursor] != "`":
            cursor += 1
            continue
        closing_length = 1
        while (
            cursor + closing_length < len(value)
            and value[cursor + closing_length] == "`"
        ):
            closing_length += 1
        if closing_length == run_length:
            return cursor + closing_length
        cursor += closing_length
    return None


def _closing_emphasis_index(
    value: str,
    start: int,
    marker: str,
) -> int | None:
    cursor = start
    while cursor < len(value) - 1:
        if value[cursor] == "\\":
            cursor += 2
            continue
        if value[cursor] == "`":
            code_end = _code_span_end(value, cursor)
            if code_end is not None:
                cursor = code_end
                continue
        if value.startswith(marker, cursor) and _can_close_emphasis(
            value,
            cursor,
            marker,
        ):
            return cursor
        cursor += 1
    return None


def _can_open_emphasis(value: str, index: int, marker: str) -> bool:
    following_index = index + len(marker)
    if following_index >= len(value) or value[following_index].isspace():
        return False
    if marker == "__" and index > 0:
        preceding = value[index - 1]
        if preceding.isalnum() or preceding == "_":
            return False
    return marker != "**" or index == 0 or value[index - 1] != "*"


def _can_close_emphasis(value: str, index: int, marker: str) -> bool:
    if index == 0 or value[index - 1].isspace():
        return False
    following_index = index + len(marker)
    if marker == "__" and following_index < len(value):
        following = value[following_index]
        if following.isalnum() or following == "_":
            return False
    return (
        marker != "**" or following_index == len(value) or value[following_index] != "*"
    )


def _flush_paragraph(
    paragraph_lines: list[str],
    rendered: list[str],
) -> None:
    if not paragraph_lines:
        return
    paragraph = " ".join(paragraph_lines)
    rendered.append(f"#par()[{render_typst_prose(paragraph)}]")
    paragraph_lines.clear()
