"""Tests of the environment file reader at its command-line seam.

The reader is run as a subprocess on files in the shape the archivematica-src
role writes: systemd ``KEY=value`` lines, with or without quotes, that a shell
must never execute.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

READER = Path(__file__).resolve().parent.parent / "files" / "fpr-env.py"

SAMPLE = """\
# The dashboard's environment, as the install role writes it.
DJANGO_SETTINGS_MODULE=archivematica.settings.production
export PATH=/usr/share/archivematica/virtualenvs/archivematica/bin:/usr/bin:/bin
ARCHIVEMATICA_DASHBOARD_DJANGO_SECRET_KEY=abc(def)$HOME&x#y`id`
ARCHIVEMATICA_DASHBOARD_CLIENT_HOST = 127.0.0.1
ARCHIVEMATICA_DASHBOARD_CLIENT_DATABASE=MCP
ARCHIVEMATICA_DASHBOARD_CLIENT_USER='archivematica'
ARCHIVEMATICA_DASHBOARD_CLIENT_PASSWORD="s3cr#t \\"quoted\\" \\\\back"
EMPTY=
; a comment in the other style
LONG=first \\
second
"""


def reader(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(READER), *args], capture_output=True, text=True
    )


def sample(tmp_path: Path) -> Path:
    path = tmp_path / "archivematica-dashboard"
    path.write_text(SAMPLE)
    return path


def test_get_reads_values_without_executing_them(tmp_path: Path) -> None:
    path = sample(tmp_path)
    assert [
        reader("get", str(path), key).stdout
        for key in (
            "ARCHIVEMATICA_DASHBOARD_DJANGO_SECRET_KEY",
            "PATH",
            "ARCHIVEMATICA_DASHBOARD_CLIENT_HOST",
            "ARCHIVEMATICA_DASHBOARD_CLIENT_USER",
            "ARCHIVEMATICA_DASHBOARD_CLIENT_PASSWORD",
            "EMPTY",
            "LONG",
        )
    ] == [
        "abc(def)$HOME&x#y`id`\n",
        "/usr/share/archivematica/virtualenvs/archivematica/bin:/usr/bin:/bin\n",
        "127.0.0.1\n",
        "archivematica\n",
        's3cr#t "quoted" \\back\n',
        "\n",
        "first second\n",
    ]


def test_get_fails_for_a_missing_key(tmp_path: Path) -> None:
    result = reader("get", str(sample(tmp_path)), "NOT_THERE")
    assert (result.returncode, result.stdout, "NOT_THERE" in result.stderr) == (
        1,
        "",
        True,
    )


def test_check_names_the_keys_a_file_lacks(tmp_path: Path) -> None:
    result = reader(
        "check",
        str(sample(tmp_path)),
        "ARCHIVEMATICA_DASHBOARD_CLIENT_HOST",
        "ARCHIVEMATICA_DASHBOARD_CLIENT_PORT",
        "EMPTY",
        "NOT_THERE",
    )
    assert (result.returncode, result.stdout) == (
        1,
        "ARCHIVEMATICA_DASHBOARD_CLIENT_PORT\nEMPTY\nNOT_THERE\n",
    )


def test_check_passes_when_every_key_is_set(tmp_path: Path) -> None:
    result = reader("check", str(sample(tmp_path)), "PATH", "LONG")
    assert (result.returncode, result.stdout) == (0, "")


def test_write_defaults_builds_the_client_file(tmp_path: Path) -> None:
    out = tmp_path / "my.cnf"
    result = reader("write-defaults", str(sample(tmp_path)), str(out))
    assert (
        result.returncode,
        result.stdout,
        out.read_text(),
        oct(out.stat().st_mode & 0o777),
    ) == (
        0,
        "MCP\n",
        "[client]\n"
        'host="127.0.0.1"\n'
        'port="3306"\n'
        'user="archivematica"\n'
        'password="s3cr#t \\"quoted\\" \\\\back"\n',
        "0o600",
    )


def test_write_defaults_names_the_missing_keys(tmp_path: Path) -> None:
    path = tmp_path / "archivematica-dashboard"
    path.write_text("ARCHIVEMATICA_DASHBOARD_CLIENT_HOST=localhost\n")
    out = tmp_path / "my.cnf"
    result = reader("write-defaults", str(path), str(out))
    assert (result.returncode, out.exists(), result.stderr.strip()) == (
        1,
        False,
        "ARCHIVEMATICA_DASHBOARD_CLIENT_DATABASE, "
        "ARCHIVEMATICA_DASHBOARD_CLIENT_USER, "
        "ARCHIVEMATICA_DASHBOARD_CLIENT_PASSWORD",
    )


def test_an_unreadable_file_is_an_error(tmp_path: Path) -> None:
    result = reader("get", str(tmp_path / "absent"), "PATH")
    assert (result.returncode, "absent" in result.stderr) == (1, True)
