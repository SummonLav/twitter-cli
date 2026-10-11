# Modified in the SummonLav fork; see FORK.md.
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from twitter_cli.models import Author, Metrics, Tweet

os.environ.setdefault("OUTPUT", "rich")


@pytest.fixture(autouse=True)
def isolated_unit_state(request, monkeypatch, tmp_path):
    """Unit tests must not use real credentials or overwrite the user's caches."""
    if request.node.get_closest_marker("smoke"):
        return

    from twitter_cli import cache
    from twitter_cli.client import TwitterClient

    for name in (
        "TWITTER_AUTH_TOKEN", "TWITTER_CT0", "TWITTER_COOKIE_STRING",
        "TWITTER_CREDENTIALS_FILE", "TWITTER_PROXY",
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(cache, "_CACHE_DIR", tmp_path / "tweet-cache")
    monkeypatch.setattr(cache, "_CACHE_FILE", tmp_path / "tweet-cache" / "last_results.json")
    monkeypatch.setattr(
        TwitterClient, "_ct_cache_path", staticmethod(lambda: str(tmp_path / "ct-cache.json")),
    )


@pytest.fixture()
def tweet_factory():
    def _make_tweet(tweet_id: str = "1", **overrides: Any) -> Tweet:
        metrics = overrides.pop(
            "metrics",
            Metrics(likes=10, retweets=2, replies=1, quotes=0, views=120, bookmarks=3),
        )
        author = overrides.pop(
            "author",
            Author(id="u1", name="Alice", screen_name="alice", verified=False),
        )
        return Tweet(
            id=tweet_id,
            text=overrides.pop("text", "hello"),
            author=author,
            metrics=metrics,
            created_at=overrides.pop("created_at", "2025-01-01"),
            media=overrides.pop("media", []),
            urls=overrides.pop("urls", []),
            is_retweet=overrides.pop("is_retweet", False),
            lang=overrides.pop("lang", "en"),
            retweeted_by=overrides.pop("retweeted_by", None),
            quoted_tweet=overrides.pop("quoted_tweet", None),
            score=overrides.pop("score", 0.0),
            article_title=overrides.pop("article_title", None),
            article_text=overrides.pop("article_text", None),
            is_subscriber_only=overrides.pop("is_subscriber_only", False),
            is_promoted=overrides.pop("is_promoted", False),
        )

    return _make_tweet


@pytest.fixture()
def fixture_loader():
    fixture_dir = Path(__file__).parent / "fixtures"

    def _load(name: str) -> Any:
        return json.loads((fixture_dir / name).read_text(encoding="utf-8"))

    return _load
