import argparse

from rich.console import Console

from music_loader.cli import collect_links, resolve_spotify_credentials

SPOTIFY = "https://open.spotify.com/album/4uLU6hMCjMI75M1A2tKUQC"
SOUNDCLOUD = "https://soundcloud.com/artist/song"


def test_links_file_skips_comments_and_duplicates(tmp_path):
    links_file = tmp_path / "links.txt"
    links_file.write_text(
        f"﻿# comment\n{SPOTIFY}\n\n{SPOTIFY}?si=dup\nnot a link\n{SOUNDCLOUD}\n",
        encoding="utf-8",
    )
    links, rejected = collect_links([str(links_file), SOUNDCLOUD], Console(quiet=True))
    assert [link.url for link in links] == [SPOTIFY, SOUNDCLOUD]
    assert rejected == ["not a link"]


def _args(client_id=None, client_secret=None):
    return argparse.Namespace(spotify_client_id=client_id, spotify_client_secret=client_secret)


def test_spotify_credentials_need_both_halves(monkeypatch):
    monkeypatch.delenv("SPOTIFY_CLIENT_ID", raising=False)
    monkeypatch.delenv("SPOTIFY_CLIENT_SECRET", raising=False)
    assert resolve_spotify_credentials(_args("id")) == (None, None)
    assert resolve_spotify_credentials(_args("id", "secret")) == ("id", "secret")


def test_spotify_credentials_fall_back_to_environment(monkeypatch):
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "env-id")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "env-secret")
    assert resolve_spotify_credentials(_args()) == ("env-id", "env-secret")
    assert resolve_spotify_credentials(_args("cli-id")) == ("cli-id", "env-secret")
