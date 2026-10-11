# Modified in the SummonLav fork; see FORK.md.
from __future__ import annotations

import io
import logging

from click.testing import CliRunner
from rich.console import Console

from twitter_cli.cli import _TerminalLogFormatter, cli
from twitter_cli.exceptions import TwitterError
from twitter_cli.models import BookmarkFolder


def _terminal(monkeypatch, *, hyperlinks: bool = False) -> io.StringIO:
    output = io.StringIO()
    monkeypatch.setattr(
        "twitter_cli.cli.console",
        Console(
            file=output, force_terminal=True, width=240, height=50,
            color_system="standard" if hyperlinks else None,
        ),
    )
    return output


def test_api_error_is_literal_text_and_exits_cleanly(monkeypatch) -> None:
    output = _terminal(monkeypatch)
    message = "server says [/red] \x1b]52;c;DUMMY\x07"

    def fail(*args, **kwargs):
        raise TwitterError(message)

    monkeypatch.setattr("twitter_cli.cli._get_client", fail)
    result = CliRunner().invoke(cli, ["user", "alice"], env={"OUTPUT": "rich"})

    assert result.exit_code == 1
    assert "server says [/red]" in output.getvalue()
    assert "\x1b" not in output.getvalue()
    assert "DUMMY" not in output.getvalue()


def test_bookmark_folder_names_cannot_create_terminal_links(monkeypatch) -> None:
    output = _terminal(monkeypatch, hyperlinks=True)
    name = "[link=https://example.invalid/login]https://x.com/settings[/link]"

    class FakeClient:
        def fetch_bookmark_folders(self):
            return [BookmarkFolder(id="1", name=name)]

    monkeypatch.setattr("twitter_cli.cli._get_client", lambda *a, **kw: FakeClient())
    result = CliRunner().invoke(cli, ["bookmarks", "folders"], env={"OUTPUT": "rich"})

    assert result.exit_code == 0, result.exception
    assert name in output.getvalue()
    assert "\x1b]8;" not in output.getvalue()


def test_write_progress_is_literal_without_changing_operation(monkeypatch) -> None:
    output = _terminal(monkeypatch)
    name = "literal[/green]"
    followed = []

    class FakeClient:
        def resolve_user_id(self, screen_name):
            assert screen_name == name
            return "123"

        def follow_user(self, user_id):
            followed.append(user_id)

    monkeypatch.setattr("twitter_cli.cli._get_client", lambda *a, **kw: FakeClient())
    result = CliRunner().invoke(cli, ["follow", name], env={"OUTPUT": "rich"})

    assert result.exit_code == 0, result.exception
    assert followed == ["123"]
    assert "✅ Now following @literal[/green]" in output.getvalue().splitlines()


def test_verbose_formatter_removes_remote_terminal_controls() -> None:
    record = logging.LogRecord(
        "twitter_cli.timeutil", logging.DEBUG, __file__, 1,
        "Cannot parse timestamp: %s", ("\x1b[2Jinvalid\x1b]52;c;DUMMY\x07",), None,
    )
    rendered = _TerminalLogFormatter("%(levelname)s: %(message)s").format(record)
    assert rendered == "DEBUG: Cannot parse timestamp: invalid"
