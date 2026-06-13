"""Tests for the command-line interface."""

from __future__ import annotations

import sidstation
from sidstation import cli


def test_cli_names(capsys, presets_path):
    rc = cli.main(["names", str(presets_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Anpanman" in out
    assert "Snare Std" in out
    assert len(out.strip().splitlines()) == 90


def test_cli_info(capsys, presets_path):
    rc = cli.main(["info", str(presets_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "patches=90" in out
    assert "Anpanman" in out


def test_cli_show(capsys, presets_path):
    rc = cli.main(["show", str(presets_path), "0"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Anpanman" in out
    assert "OSC1" in out
    assert "TABLE1" in out


def test_cli_show_out_of_range(capsys, presets_path):
    rc = cli.main(["show", str(presets_path), "999"])
    assert rc == 2
    assert "out of range" in capsys.readouterr().out


def test_cli_no_command_prints_help(capsys):
    rc = cli.main([])
    assert rc == 1
    assert "usage" in capsys.readouterr().out.lower()


def test_cli_version(capsys):
    try:
        cli.main(["--version"])
    except SystemExit as exc:
        assert exc.code == 0
    out = capsys.readouterr().out
    assert sidstation.__version__ in out
