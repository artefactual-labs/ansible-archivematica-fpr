#!/usr/bin/env python3
"""Reads the environment files the archivematica-src role writes, without
executing them.

Usage:
  fpr-env.py get FILE KEY              print the value of KEY
  fpr-env.py check FILE KEY...         print the keys FILE leaves unset or
                                       empty; exit 1 when there is any
  fpr-env.py write-defaults FILE OUT   write a mysql client defaults file
                                       (0600) from the
                                       ARCHIVEMATICA_DASHBOARD_CLIENT_* keys of
                                       FILE and print the database name

The files are systemd environment files: KEY=value lines, an optional
"export", quotes around a value, backslash escapes inside double quotes, a
trailing backslash continuing the line, and comment lines starting with # or
;. Runs on the host's own interpreter, Python 3.6 or later.
"""

import io
import os
import sys
from typing import Dict
from typing import List
from typing import Optional

CLIENT_KEYS = (
    "ARCHIVEMATICA_DASHBOARD_CLIENT_HOST",
    "ARCHIVEMATICA_DASHBOARD_CLIENT_DATABASE",
    "ARCHIVEMATICA_DASHBOARD_CLIENT_USER",
    "ARCHIVEMATICA_DASHBOARD_CLIENT_PASSWORD",
)
PORT_KEY = "ARCHIVEMATICA_DASHBOARD_CLIENT_PORT"
DEFAULT_PORT = "3306"


def logical_lines(text: str) -> List[str]:
    """The lines of the file with backslash continuations joined."""
    lines: List[str] = []
    pending: Optional[str] = None
    for raw in text.splitlines():
        line = raw if pending is None else pending + raw
        if line.endswith("\\"):
            pending = line[:-1]
            continue
        lines.append(line)
        pending = None
    if pending is not None:
        lines.append(pending)
    return lines


def unquote(value: str) -> str:
    """The value without its surrounding quotes; backslash escapes count
    inside double quotes only."""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        inner = value[1:-1]
        if value[0] == "'":
            return inner
        unescaped = []
        index = 0
        while index < len(inner):
            char = inner[index]
            if char == "\\" and index + 1 < len(inner):
                index += 1
                char = inner[index]
            unescaped.append(char)
            index += 1
        return "".join(unescaped)
    return value


def parse(text: str) -> Dict[str, str]:
    """The assignments of an environment file, later ones overriding."""
    values: Dict[str, str] = {}
    for line in logical_lines(text):
        stripped = line.strip()
        if not stripped or stripped[0] in "#;" or "=" not in stripped:
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export ") :]
        key, _, value = stripped.partition("=")
        key = key.strip()
        if key:
            values[key] = unquote(value)
    return values


def read(path: str) -> Dict[str, str]:
    try:
        with io.open(path, encoding="utf-8", errors="surrogateescape") as handle:
            return parse(handle.read())
    except (IOError, OSError) as error:
        fail("cannot read {}: {}".format(path, error.strerror or error))
        return {}


def fail(message: str) -> None:
    sys.stderr.write(message + "\n")
    sys.exit(1)


def emit(text: str) -> None:
    sys.stdout.buffer.write(text.encode("utf-8", "surrogateescape"))
    sys.stdout.flush()


def quote_option(value: str) -> str:
    """The value as a double-quoted mysql option file value."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def command_get(path: str, key: str) -> None:
    values = read(path)
    if key not in values:
        fail("{} is not set in {}".format(key, path))
    emit(values[key] + "\n")


def command_check(path: str, keys: List[str]) -> None:
    values = read(path)
    missing = [key for key in keys if not values.get(key)]
    for key in missing:
        emit(key + "\n")
    if missing:
        sys.exit(1)


def command_write_defaults(path: str, out: str) -> None:
    values = read(path)
    missing = [key for key in CLIENT_KEYS if not values.get(key)]
    if missing:
        fail(", ".join(missing))
    host, database, user, password = (values[key] for key in CLIENT_KEYS)
    port = values.get(PORT_KEY) or DEFAULT_PORT
    content = "[client]\n" + "".join(
        "{}={}\n".format(name, quote_option(value))
        for name, value in (
            ("host", host),
            ("port", port),
            ("user", user),
            ("password", password),
        )
    )
    descriptor = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        os.write(descriptor, content.encode("utf-8", "surrogateescape"))
    finally:
        os.close(descriptor)
    emit(database + "\n")


def main(argv: List[str]) -> None:
    if len(argv) >= 3 and argv[0] == "get" and len(argv) == 3:
        command_get(argv[1], argv[2])
    elif len(argv) >= 3 and argv[0] == "check":
        command_check(argv[1], argv[2:])
    elif len(argv) == 3 and argv[0] == "write-defaults":
        command_write_defaults(argv[1], argv[2])
    else:
        fail(__doc__ or "usage: fpr-env.py get|check|write-defaults ...")


if __name__ == "__main__":
    main(sys.argv[1:])
