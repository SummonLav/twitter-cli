# Modified in the SummonLav fork; see FORK.md.
from __future__ import annotations

from io import StringIO

import pytest
from rich.console import Console
from rich.text import Text

from twitter_cli.formatter import (
    article_to_markdown,
    print_article,
    print_tweet_detail,
    print_tweet_table,
    print_user_profile,
    print_user_table,
    sanitize_terminal_text,
)
from twitter_cli.models import Author, TweetMedia, UserProfile


def _console(*, links: bool = False) -> tuple[Console, StringIO]:
    output = StringIO()
    # Rich emits hyperlinks only when terminal styling is enabled. Specify both
    # dimensions so TERM=dumb on CI cannot force a narrow 80-column layout.
    console = Console(
        file=output, force_terminal=True, color_system="standard" if links else None,
        width=240, height=40,
    )
    return console, output


@pytest.mark.parametrize(
    "sequence",
    [
        "\x1b[2J\x1b[H",  # clear screen and move cursor
        "\x1b]52;c;c3ludGhldGlj\x07",  # clipboard, BEL terminator
        "\x1b]52;c;c3ludGhldGlj\x1b\\",  # clipboard, ST terminator
        "\x1b]8;;https://example.invalid\x1b\\",  # hyperlink
        "\x9d52;c;c3ludGhldGlj\x9c",  # 8-bit OSC/ST
        "\x9b2J",  # 8-bit CSI
        "\x1bPpayload\x1b\\",  # DCS
        "\x1b_payload\x1b\\",  # APC
        "\x1b^payload\x1b\\",  # PM
        "\x1bXpayload\x1b\\",  # SOS
        "\x90payload\x9c",  # 8-bit DCS
        "\x1b(0",  # character-set selection
        "\x00\x07\x08\x0b\x0c\x0d\x1b\x7f\x85\x9c",  # malformed/control bytes
    ],
)
def test_terminal_sequences_cannot_reach_terminal(sequence: str) -> None:
    assert sanitize_terminal_text("before" + sequence + "after") == "beforeafter"


@pytest.mark.parametrize("sequence", ["\x1b]52;c;payload", "\x1bPpayload", "\x1b[31"])
def test_unterminated_terminal_sequences_are_removed(sequence: str) -> None:
    assert sanitize_terminal_text("before" + sequence) == "before"


def test_sanitizer_preserves_unicode_markdown_and_whitespace() -> None:
    text = "中文 👩‍💻 café\n\t**bold** [site](https://example.org)\n```python\nx = 1\n```"
    assert sanitize_terminal_text(text) == text


def test_malformed_nested_c1_sequences_are_neutralized() -> None:
    assert sanitize_terminal_text("before\x9d\x9d\x90\x9f\x1b[2Jafter") == "beforeafter"


@pytest.mark.parametrize("renderer", [print_tweet_table, print_tweet_detail])
def test_tweet_body_rich_markup_is_literal(renderer, tweet_factory) -> None:
    body = "[/red] [link=https://example.invalid]https://x.com[/link]"
    tweet = tweet_factory(text=body)
    console, output = _console(links=True)
    renderer([tweet] if renderer is print_tweet_table else tweet, console=console)
    rendered = output.getvalue()
    assert body in rendered
    assert "\x1b]8;" not in rendered


@pytest.mark.parametrize(
    "field",
    ["name", "screen_name", "created_at", "retweeted_by", "quote", "media", "url"],
)
def test_tweet_detail_metadata_is_literal_and_has_no_terminal_commands(field, tweet_factory) -> None:
    value = "[/red]\x1b]52;c;c3ludGhldGlj\x07"
    tweet = tweet_factory()
    if field in {"name", "screen_name"}:
        setattr(tweet.author, field, value)
    elif field == "retweeted_by":
        tweet.is_retweet = True
        tweet.retweeted_by = value
    elif field == "quote":
        tweet.quoted_tweet = tweet_factory(text=value)
    elif field == "media":
        tweet.media = [TweetMedia(type="photo", url=value)]
    elif field == "url":
        tweet.urls = [value]
    else:
        setattr(tweet, field, value)
    console, output = _console()
    print_tweet_detail(tweet, console=console)
    assert "[/red]" in output.getvalue()
    assert "\x1b" not in output.getvalue()
    assert "c3ludGhldGlj" not in output.getvalue()


def test_tweet_table_cleans_text_before_truncation(tweet_factory) -> None:
    tweet = tweet_factory(text="\x1b]52;c;" + "x" * 200 + "\x07visible body")
    console, output = _console()
    print_tweet_table([tweet], console=console, title="[/red]\x1b[2J")
    assert "visible body" in output.getvalue()
    assert "[/red]" in output.getvalue()
    assert "\x1b" not in output.getvalue()


def test_unterminated_body_sequence_cannot_hide_following_metadata(tweet_factory) -> None:
    tweet = tweet_factory(text="visible\x1b]52;c;unterminated")
    console, output = _console()
    print_tweet_detail(tweet, console=console)
    assert "visible" in output.getvalue()
    assert "unterminated" not in output.getvalue()
    assert "https://x.com/alice/status/1" in output.getvalue()


@pytest.mark.parametrize("renderer", [print_user_profile, print_user_table])
def test_user_fields_and_table_title_are_literal(renderer) -> None:
    value = "[/red]\x1b]52;c;c3ludGhldGlj\x07"
    user = UserProfile(
        id="1", name=value, screen_name=value, bio=value, location=value, url=value,
        created_at=value,
    )
    console, output = _console()
    if renderer is print_user_table:
        renderer([user], console=console, title=value)
    else:
        renderer(user, console=console)
    assert "[/red]" in output.getvalue()
    assert "\x1b" not in output.getvalue()
    assert "c3ludGhldGlj" not in output.getvalue()


def test_user_bio_markup_cannot_create_terminal_link() -> None:
    bio = "[link=https://example.invalid]https://x.com[/link]"
    user = UserProfile(id="1", name="Alice", screen_name="alice", bio=bio)
    for renderer in (print_user_profile, print_user_table):
        console, output = _console(links=True)
        renderer([user] if renderer is print_user_table else user, console=console)
        assert bio in output.getvalue()
        assert "\x1b]8;" not in output.getvalue()


def test_article_keeps_markdown_but_sanitizes_terminal_commands(tweet_factory) -> None:
    article = tweet_factory(
        article_title="[/red]\x1b[2J",
        author=Author(id="1", name="[/red]", screen_name="alice"),
        article_text=(
            "## Heading\n\n**bold**\n\n"
            "[site](https://example.org)\n\n"
            "```python\nprint('hello')\n```\n"
            "\x1b]52;c;c3ludGhldGlj\x07"
        ),
    )
    console, output = _console(links=True)
    print_article(article, console=console)
    rendered = output.getvalue()
    plain = Text.from_ansi(rendered).plain
    assert "[/red]" in plain  # metadata is literal, never Rich markup
    assert "Heading" in plain
    assert "**bold**" not in plain
    assert "bold" in plain
    assert "print('hello')" in plain
    assert "\x1b]52;" not in rendered
    assert "\x1b[2J" not in rendered
    assert "c3ludGhldGlj" not in rendered
    assert "https://example.org" in rendered  # normal Markdown links still work


def test_markdown_export_removes_terminal_controls_without_changing_markdown(tweet_factory) -> None:
    article = tweet_factory(
        article_title="Title\x1b[2J",
        author=Author(id="1", name="Alice\x1b[H", screen_name="alice"),
        article_text="**bold**\n\n[site](https://example.org)\x1b]52;c;payload\x07",
    )
    exported = article_to_markdown(article)
    assert "# Title\n" in exported
    assert "(Alice)" in exported
    assert "**bold**\n\n[site](https://example.org)\n" in exported
    assert "\x1b" not in exported
    assert "payload" not in exported


@pytest.mark.parametrize("entity", ["&#x1b;", "&#27;", "&#x9d;", "&#157;"])
@pytest.mark.parametrize("location", ["body", "label", "target"])
def test_article_html_entities_cannot_reintroduce_terminal_commands(
    entity: str, location: str, tweet_factory,
) -> None:
    payload = entity + "]52;c;synthetic"
    if location == "label":
        payload = "[%s](https://example.org)" % payload
    elif location == "target":
        payload = "[site](https://example.org/%s)" % payload
    article = tweet_factory(article_text=payload)
    console, output = _console(links=True)
    print_article(article, console=console)
    rendered = output.getvalue()
    assert "\x1b]52;" not in rendered
    assert not any(0x7F <= ord(char) < 0xA0 for char in rendered)
    # markdown-it rejects control entities in visible text rather than emitting
    # their characters; URL targets are normalized without decoding controls.
    if location != "target":
        assert "\ufffd" in rendered
