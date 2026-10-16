#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Inventory of an Archivematica Format Policy Registry.

Turns the two files the archivematica_fpr role collects on a host, the raw
registry dump (``fpr-raw.json``) and the executable probe (``probe.json``),
into an inventory JSON document and a Markdown report. Runs on the controller
with the standard library only::

    uv run --script fpr_inventory.py build --raw fpr-raw.json --probe probe.json \\
        --meta meta.json --out DIR [--all] [--format json|markdown|both]

``collect`` dumps a reachable database into the same raw document without
Ansible (``uv run --with pymysql --script fpr_inventory.py collect ...``) and
``compare`` is reserved for the baseline comparison.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from typing import TypedDict

REPORTER_VERSION = "1"
CONTRACT_VERSION = 1

DUMP_TABLES = (
    "fpcommand",
    "fptool",
    "fprule",
    "formatversion",
    "format",
    "formatgroup",
    "idcommand",
    "idtool",
    "idrule",
)


class ReporterError(Exception):
    """A problem with the input files that the operator has to fix."""


# --- the registry: rows of the dump, typed ------------------------------------

Row = dict[str, object]


@dataclass(frozen=True, slots=True)
class FPCommand:
    id: int
    uuid: str
    replaces: str | None
    enabled: bool
    lastmodified: str
    description: str
    command: str
    script_type: str
    output_location: str | None
    command_usage: str
    tool: str | None
    output_format: str | None
    verification_command: str | None
    event_detail_command: str | None


@dataclass(frozen=True, slots=True)
class FPTool:
    id: int
    uuid: str
    description: str
    version: str
    enabled: bool
    slug: str


@dataclass(frozen=True, slots=True)
class FPRule:
    id: int
    uuid: str
    replaces: str | None
    enabled: bool
    lastmodified: str
    purpose: str
    count_attempts: int
    count_okay: int
    count_not_okay: int
    command: str
    format: str


@dataclass(frozen=True, slots=True)
class FormatVersion:
    id: int
    uuid: str
    replaces: str | None
    enabled: bool
    lastmodified: str
    version: str
    pronom_id: str
    description: str
    access_format: bool
    preservation_format: bool
    slug: str
    format: str


@dataclass(frozen=True, slots=True)
class Format:
    id: int
    uuid: str
    description: str
    slug: str
    group: str


@dataclass(frozen=True, slots=True)
class FormatGroup:
    id: int
    uuid: str
    description: str
    slug: str


@dataclass(frozen=True, slots=True)
class IDCommand:
    id: int
    uuid: str
    replaces: str | None
    enabled: bool
    lastmodified: str
    description: str
    config: str
    script: str
    script_type: str
    tool: str | None


@dataclass(frozen=True, slots=True)
class IDTool:
    id: int
    uuid: str
    description: str
    version: str
    enabled: bool
    slug: str


@dataclass(frozen=True, slots=True)
class IDRule:
    id: int
    uuid: str
    replaces: str | None
    enabled: bool
    lastmodified: str
    command_output: str
    command: str
    format: str


@dataclass(frozen=True, slots=True)
class Migration:
    id: int
    app: str
    name: str
    applied: str


@dataclass(frozen=True, slots=True)
class Registry:
    dumped_at: str
    commands: list[FPCommand]
    tools: list[FPTool]
    rules: list[FPRule]
    format_versions: list[FormatVersion]
    formats: list[Format]
    format_groups: list[FormatGroup]
    id_commands: list[IDCommand]
    id_tools: list[IDTool]
    id_rules: list[IDRule]
    migrations: list[Migration]


def _object(value: object, where: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ReporterError(f"{where}: expected a JSON object")
    return {str(key): item for key, item in value.items()}


def _rows(document: dict[str, object], table: str) -> list[Row]:
    value = document.get(table)
    if value is None:
        return []
    if not isinstance(value, list):
        raise ReporterError(f"{table}: expected a list of rows")
    return [_object(item, f"{table}[{index}]") for index, item in enumerate(value)]


def _text(row: Row, key: str) -> str:
    value = row.get(key)
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool | int | float):
        return str(value)
    raise ReporterError(f"{key}: expected text, got {type(value).__name__}")


def _optional_text(row: Row, key: str) -> str | None:
    value = row.get(key)
    if value is None or value == "":
        return None
    return _text(row, key)


def _number(row: Row, key: str) -> int:
    value = row.get(key)
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value)
    raise ReporterError(f"{key}: expected a number, got {value!r}")


def _flag(row: Row, key: str) -> bool:
    value = row.get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    if value is None:
        return False
    raise ReporterError(f"{key}: expected a flag, got {value!r}")


def _command_row(row: Row) -> FPCommand:
    return FPCommand(
        id=_number(row, "id"),
        uuid=_text(row, "uuid"),
        replaces=_optional_text(row, "replaces"),
        enabled=_flag(row, "enabled"),
        lastmodified=_text(row, "lastmodified"),
        description=_text(row, "description"),
        command=_text(row, "command"),
        script_type=_text(row, "script_type"),
        output_location=_optional_text(row, "output_location"),
        command_usage=_text(row, "command_usage"),
        tool=_optional_text(row, "tool"),
        output_format=_optional_text(row, "output_format"),
        verification_command=_optional_text(row, "verification_command"),
        event_detail_command=_optional_text(row, "event_detail_command"),
    )


def _tool_row(row: Row) -> FPTool:
    return FPTool(
        id=_number(row, "id"),
        uuid=_text(row, "uuid"),
        description=_text(row, "description"),
        version=_text(row, "version"),
        enabled=_flag(row, "enabled"),
        slug=_text(row, "slug"),
    )


def _rule_row(row: Row) -> FPRule:
    return FPRule(
        id=_number(row, "id"),
        uuid=_text(row, "uuid"),
        replaces=_optional_text(row, "replaces"),
        enabled=_flag(row, "enabled"),
        lastmodified=_text(row, "lastmodified"),
        purpose=_text(row, "purpose"),
        count_attempts=_number(row, "count_attempts"),
        count_okay=_number(row, "count_okay"),
        count_not_okay=_number(row, "count_not_okay"),
        command=_text(row, "command"),
        format=_text(row, "format"),
    )


def _format_version_row(row: Row) -> FormatVersion:
    return FormatVersion(
        id=_number(row, "id"),
        uuid=_text(row, "uuid"),
        replaces=_optional_text(row, "replaces"),
        enabled=_flag(row, "enabled"),
        lastmodified=_text(row, "lastmodified"),
        version=_text(row, "version"),
        pronom_id=_text(row, "pronom_id"),
        description=_text(row, "description"),
        access_format=_flag(row, "access_format"),
        preservation_format=_flag(row, "preservation_format"),
        slug=_text(row, "slug"),
        format=_text(row, "format"),
    )


def _format_row(row: Row) -> Format:
    return Format(
        id=_number(row, "id"),
        uuid=_text(row, "uuid"),
        description=_text(row, "description"),
        slug=_text(row, "slug"),
        group=_text(row, "group"),
    )


def _format_group_row(row: Row) -> FormatGroup:
    return FormatGroup(
        id=_number(row, "id"),
        uuid=_text(row, "uuid"),
        description=_text(row, "description"),
        slug=_text(row, "slug"),
    )


def _id_command_row(row: Row) -> IDCommand:
    return IDCommand(
        id=_number(row, "id"),
        uuid=_text(row, "uuid"),
        replaces=_optional_text(row, "replaces"),
        enabled=_flag(row, "enabled"),
        lastmodified=_text(row, "lastmodified"),
        description=_text(row, "description"),
        config=_text(row, "config"),
        script=_text(row, "script"),
        script_type=_text(row, "script_type"),
        tool=_optional_text(row, "tool"),
    )


def _id_tool_row(row: Row) -> IDTool:
    return IDTool(
        id=_number(row, "id"),
        uuid=_text(row, "uuid"),
        description=_text(row, "description"),
        version=_text(row, "version"),
        enabled=_flag(row, "enabled"),
        slug=_text(row, "slug"),
    )


def _id_rule_row(row: Row) -> IDRule:
    return IDRule(
        id=_number(row, "id"),
        uuid=_text(row, "uuid"),
        replaces=_optional_text(row, "replaces"),
        enabled=_flag(row, "enabled"),
        lastmodified=_text(row, "lastmodified"),
        command_output=_text(row, "command_output"),
        command=_text(row, "command"),
        format=_text(row, "format"),
    )


def _migration_row(row: Row) -> Migration:
    return Migration(
        id=_number(row, "id"),
        app=_text(row, "app"),
        name=_text(row, "name"),
        applied=_text(row, "applied"),
    )


def load_registry(path: Path) -> Registry:
    """Read a raw dump and normalize its rows."""
    document = _object(_load_json(path), str(path))
    missing = [table for table in DUMP_TABLES if table not in document]
    if missing:
        raise ReporterError(
            f"{path}: not a registry dump, missing {', '.join(missing)}"
        )
    return Registry(
        dumped_at=_text(document, "dumped_at"),
        commands=[_command_row(row) for row in _rows(document, "fpcommand")],
        tools=[_tool_row(row) for row in _rows(document, "fptool")],
        rules=[_rule_row(row) for row in _rows(document, "fprule")],
        format_versions=[
            _format_version_row(row) for row in _rows(document, "formatversion")
        ],
        formats=[_format_row(row) for row in _rows(document, "format")],
        format_groups=[
            _format_group_row(row) for row in _rows(document, "formatgroup")
        ],
        id_commands=[_id_command_row(row) for row in _rows(document, "idcommand")],
        id_tools=[_id_tool_row(row) for row in _rows(document, "idtool")],
        id_rules=[_id_rule_row(row) for row in _rows(document, "idrule")],
        migrations=[_migration_row(row) for row in _rows(document, "migrations")],
    )


def _load_json(path: Path) -> object:
    try:
        with path.open(encoding="utf-8") as handle:
            loaded: object = json.load(handle)
    except OSError as error:
        raise ReporterError(f"{path}: {error.strerror}") from error
    except ValueError as error:
        raise ReporterError(f"{path}: not valid JSON ({error})") from error
    return loaded


# --- the probe ----------------------------------------------------------------


class ProbeExecutable(TypedDict):
    path: str | None
    version: str | None


class ProbeCheck(TypedDict):
    id: str
    subject: str
    result: str
    output: str | None


class ProbeInterpreter(TypedDict):
    name: str | None
    path: str | None
    version: str | None


class ProbeCheckout(TypedDict):
    path: str | None
    description: str | None
    branch: str | None


class ProbeArchivematica(TypedDict):
    version: str | None
    source: str | None
    location: str | None
    checkout: ProbeCheckout | None


@dataclass(frozen=True, slots=True)
class Probe:
    probed_at: str
    host: str | None
    user: str | None
    os: str | None
    path: str | None
    interpreter: ProbeInterpreter | None
    archivematica: ProbeArchivematica | None
    executables: dict[str, ProbeExecutable]
    decoders: frozenset[str] | None
    encoders: frozenset[str] | None
    checks: list[ProbeCheck]
    document: dict[str, object]


def _names(value: object, where: str) -> frozenset[str]:
    if not isinstance(value, list):
        raise ReporterError(f"{where}: expected a list of names")
    return frozenset(str(item) for item in value)


def load_probe(path: Path) -> Probe:
    """Read a probe document."""
    document = _object(_load_json(path), str(path))
    if "executables" not in document:
        raise ReporterError(f"{path}: not a probe document, missing executables")
    executables: dict[str, ProbeExecutable] = {}
    for name, entry in _object(document["executables"], "executables").items():
        row = _object(entry, f"executables[{name}]")
        executables[name] = {
            "path": _optional_text(row, "path"),
            "version": _optional_text(row, "version"),
        }
    interpreter: ProbeInterpreter | None = None
    if document.get("interpreter") is not None:
        row = _object(document["interpreter"], "interpreter")
        interpreter = {
            "name": _optional_text(row, "name"),
            "path": _optional_text(row, "path"),
            "version": _optional_text(row, "version"),
        }
    archivematica: ProbeArchivematica | None = None
    if document.get("archivematica") is not None:
        row = _object(document["archivematica"], "archivematica")
        checkout: ProbeCheckout | None = None
        if row.get("checkout") is not None:
            found = _object(row["checkout"], "archivematica.checkout")
            checkout = {
                "path": _optional_text(found, "path"),
                "description": _optional_text(found, "description"),
                "branch": _optional_text(found, "branch"),
            }
        archivematica = {
            "version": _optional_text(row, "version"),
            "source": _optional_text(row, "source"),
            "location": _optional_text(row, "location"),
            "checkout": checkout,
        }
    decoders: frozenset[str] | None = None
    encoders: frozenset[str] | None = None
    if document.get("ffmpeg") is not None:
        codecs = _object(document["ffmpeg"], "ffmpeg")
        if codecs.get("decoders") is not None:
            decoders = _names(codecs["decoders"], "ffmpeg.decoders")
        if codecs.get("encoders") is not None:
            encoders = _names(codecs["encoders"], "ffmpeg.encoders")
    checks: list[ProbeCheck] = []
    for item in _rows(document, "checks"):
        checks.append(
            {
                "id": _text(item, "id"),
                "subject": _text(item, "subject"),
                "result": _text(item, "result"),
                "output": _optional_text(item, "output"),
            }
        )
    return Probe(
        probed_at=_text(document, "probed_at"),
        host=_optional_text(document, "host"),
        user=_optional_text(document, "user"),
        os=_optional_text(document, "os"),
        path=_optional_text(document, "path"),
        interpreter=interpreter,
        archivematica=archivematica,
        executables=executables,
        decoders=decoders,
        encoders=encoders,
        checks=checks,
        document=document,
    )


# --- labels, as the fpr models spell their choices ----------------------------

USAGE_LABELS = {
    "characterization": "Characterization",
    "event_detail": "Event detail",
    "extraction": "Extraction",
    "normalization": "Normalization",
    "transcription": "Transcription",
    "validation": "Validation",
    "verification": "Verification",
}
LINKED_USAGES = frozenset({"event_detail", "verification"})
SCRIPT_TYPE_LABELS = {
    "command": "Command line",
    "bashScript": "Bash script",
    "pythonScript": "Python script",
    "as_is": "No shebang needed",
}
PURPOSE_LABELS = {
    "access": "Access",
    "characterization": "Characterization",
    "extract": "Extract",
    "preservation": "Preservation",
    "thumbnail": "Thumbnail",
    "transcription": "Transcription",
    "validation": "Validation",
    "policy_check": "Validation against a policy",
    "default_access": "Default access",
    "default_characterization": "Default characterization",
    "default_thumbnail": "Default thumbnail",
}
ID_CONFIG_LABELS = {"PUID": "PUID", "MIME": "MIME type", "ext": "File extension"}


Status = Literal["enabled", "enabled-superseded", "superseded", "disabled"]
Verdict = Literal["present", "missing", "not probed"]


def command_status(enabled: bool, replaced: bool) -> Status:
    """The classification of a versioned row: enabled, enabled-superseded,
    superseded or disabled."""
    if enabled and replaced:
        return "enabled-superseded"
    if enabled:
        return "enabled"
    if replaced:
        return "superseded"
    return "disabled"


def format_version_label(format_version: FormatVersion) -> str:
    label = format_version.description.strip()
    version = format_version.version.strip()
    if version and version not in label:
        label = f"{label} {version}".strip()
    return label


# --- what a command runs ------------------------------------------------------

# Programs the stock commands start; the probe's executable list extends it.
KNOWN_EXECUTABLES = frozenset(
    {
        "7z",
        "convert",
        "exiftool",
        "ffmpeg",
        "ffprobe",
        "fido",
        "fiwalk",
        "gs",
        "inkscape",
        "jhove",
        "libreoffice",
        "magick",
        "mediaconch",
        "mediainfo",
        "ps2pdf",
        "python",
        "python3",
        "readpst",
        "sf",
        "soffice",
        "tesseract",
        "tsk_recover",
        "unrar",
        "unrar-nonfree",
    }
)
# Coreutils and shell words listed per command, never probed.
SHELL_UTILITIES = frozenset(
    {
        "awk",
        "basename",
        "cat",
        "chmod",
        "cp",
        "cut",
        "dirname",
        "echo",
        "eval",
        "find",
        "flock",
        "grep",
        "head",
        "ls",
        "mkdir",
        "mv",
        "rm",
        "sed",
        "sort",
        "sudo",
        "tail",
        "test",
        "tr",
        "xargs",
    }
)
# Python libraries that drive an executable on the command's behalf.
LIBRARY_EXECUTABLES = {"ammcpc": "mediaconch"}
SHELL_SCRIPT_TYPES = frozenset({"bashScript", "command", "as_is"})
COMMAND_WRAPPERS = frozenset(
    {"sudo", "eval", "exec", "nohup", "time", "env", "nice", "ionice", "xargs"}
)
COMMAND_STRING_FLAGS = frozenset({"-c"})
COMMAND_POSITION_ENDINGS = ("\n", "|", ";", "&&", "||", "`", "$(", "(", "{", "[")
# What may precede a quoted program name: a list element, an assigned value, a
# separator; not a function call or an echo, whose strings are sentences.
QUOTE_LEADER_ENDINGS = ("\n", "|", ";", "&&", "||", "`", "$(", "{", "[", "=")
ASSIGNMENT_WORD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
DURATION_RE = re.compile(r"^[0-9]+(\.[0-9]+)?[smhd]?$")
TOKEN_RE = re.compile(r"[A-Za-z0-9_./-]+")
IMPORT_RE = re.compile(
    r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", re.MULTILINE
)
PLACEHOLDER_RE = re.compile(r"%([A-Za-z]+)%")


def in_command_position(text: str, start: int) -> bool:
    """Whether a shell would read the token at ``start`` as a program name: at
    the start of a line, after a pipe, a separator or a subshell opening,
    after a wrapper such as sudo, a timeout or an assignment prefix such as
    LC_ALL=C, or first inside a quoted string that is a list element, an
    assigned value or a ``-c`` command string rather than a sentence."""
    before = text[:start].rstrip(" \t")
    if before.endswith(('"', "'")):
        if before.endswith(('\\"', "\\'")):
            return False
        lead = before[:-1].rstrip(" \t")
        if lead == "" or lead.endswith(QUOTE_LEADER_ENDINGS):
            return True
        return lead.split()[-1] in COMMAND_STRING_FLAGS | COMMAND_WRAPPERS
    while True:
        if before == "" or before.endswith(COMMAND_POSITION_ENDINGS):
            return True
        words = before.split()
        last = words[-1]
        if last in COMMAND_WRAPPERS:
            return True
        if ASSIGNMENT_WORD_RE.match(last):
            before = before[: -len(last)].rstrip(" \t")
        elif DURATION_RE.match(last) and len(words) > 1 and words[-2] == "timeout":
            before = before[: -len(last)].rstrip(" \t")
            before = before[: -len("timeout")].rstrip(" \t")
        else:
            return False


def detect_calls(
    text: str, script_type: str, executables: frozenset[str]
) -> tuple[list[str], list[str]]:
    """Executables and shell utilities a command starts, in order of appearance.

    Absolute paths count by their basename. Shell utilities are only listed for
    the script types a shell runs."""
    found: list[str] = []
    shell: list[str] = []
    for match in TOKEN_RE.finditer(text):
        if not in_command_position(text, match.start()):
            continue
        name = match.group(0).rsplit("/", 1)[-1]
        if name in executables:
            if name not in found:
                found.append(name)
        elif (
            name in SHELL_UTILITIES
            and script_type in SHELL_SCRIPT_TYPES
            and name not in shell
        ):
            shell.append(name)
    return found, shell


def detect_imports(text: str, script_type: str) -> list[str]:
    """Top-level modules a Python script imports, standard library excluded."""
    if script_type != "pythonScript":
        return []
    modules: list[str] = []
    for match in IMPORT_RE.finditer(text):
        module = (match.group(1) or match.group(2)).split(".")[0]
        if module not in modules and module not in sys.stdlib_module_names:
            modules.append(module)
    return modules


def detect_placeholders(*texts: str | None) -> list[str]:
    """The ``%name%`` placeholders the texts use, sorted."""
    found: set[str] = set()
    for text in texts:
        if text:
            found.update(PLACEHOLDER_RE.findall(text))
    return sorted(found)


def absolute_program_paths(text: str, executables: frozenset[str]) -> list[str]:
    """Programs the text starts by absolute path rather than by name."""
    paths: list[str] = []
    for match in TOKEN_RE.finditer(text):
        token = match.group(0)
        if not token.startswith("/") or not in_command_position(text, match.start()):
            continue
        name = token.rsplit("/", 1)[-1]
        if (name in executables or name in SHELL_UTILITIES) and token not in paths:
            paths.append(token)
    return paths


# --- options the commands pass ------------------------------------------------

ASSIGNMENT_RE = re.compile(
    r"""^\s*(\w+)=(?:"((?:[^"\\\n]|\\.)*)"|'([^'\n]*)'|([^\s"']+))\s*$""",
    re.MULTILINE,
)
VARIABLE_RE = re.compile(r"\$\{(\w+)\}|\$(\w+)")
OPTION_TOKEN_RE = re.compile(r"[A-Za-z0-9_.:/${}%-]+")
SEGMENT_RE = re.compile(r"\n|;|\|\||\||&&")
CODEC_OPTIONS = frozenset({"-c", "-codec", "-vcodec", "-acodec", "-scodec"})
CODEC_OPTION_PREFIXES = ("-c:", "-codec:")


def substitute_shell_variables(text: str) -> str:
    """Replaces ``${name}`` and ``$name`` with the value of the simple
    assignment ``name="value"`` found in the text, so that codec names held in
    variables can be read. A value holding variables itself is resolved by the
    second pass, once what it holds has been replaced."""
    return _substitute_once(_substitute_once(text))


def _substitute_once(text: str) -> str:
    values: dict[str, str] = {}
    for match in ASSIGNMENT_RE.finditer(text):
        value = next(group for group in match.groups()[1:] if group is not None)
        if "$" not in value:
            values.setdefault(match.group(1), value)

    def replace(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(2)
        return values.get(name, match.group(0))

    return VARIABLE_RE.sub(replace, text)


def is_codec_option(token: str) -> bool:
    return token in CODEC_OPTIONS or token.startswith(CODEC_OPTION_PREFIXES)


def ffmpeg_codecs(text: str) -> tuple[list[str], list[str]]:
    """The decoders the text's ffmpeg invocations force (a codec option before
    ``-i``) and the encoders they name (a codec option after ``-i``), read per
    command segment so that a later command is never taken for an option."""
    decoders: list[str] = []
    encoders: list[str] = []
    for segment in SEGMENT_RE.split(substitute_shell_variables(text)):
        tokens = OPTION_TOKEN_RE.findall(segment)
        stage = ""
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if token.rsplit("/", 1)[-1] == "ffmpeg":
                stage = "input"
            elif stage and token == "-i":
                stage = "output"
            elif stage and is_codec_option(token) and index + 1 < len(tokens):
                name = tokens[index + 1]
                index += 1
                if name != "copy" and not name.startswith(("-", "$", "%")):
                    target = decoders if stage == "input" else encoders
                    if name not in target:
                        target.append(name)
            index += 1
    return decoders, encoders


def program_options(text: str, program: str) -> list[str]:
    """The options (``-x`` and ``--long``, without values) the text passes to
    the program, on every line that starts it."""
    options: list[str] = []
    for line in text.splitlines():
        tokens = OPTION_TOKEN_RE.findall(line)
        starts = [
            i for i, token in enumerate(tokens) if token.rsplit("/", 1)[-1] == program
        ]
        if not starts:
            continue
        for token in tokens[starts[0] + 1 :]:
            if token.startswith("-") and token not in options:
                options.append(token)
    return options


# --- derived commands ---------------------------------------------------------


class RuleFormatEntry(TypedDict):
    uuid: str
    enabled: bool
    status: Status
    replaces: str | None
    replaced_by: list[str]
    format_version: str
    format_version_enabled: bool
    pronom_id: str
    format: str
    group: str
    attempts: int
    okay: int
    not_okay: int


class PurposeEntry(TypedDict):
    purpose: str
    label: str
    enabled: int
    disabled: int
    formats: list[RuleFormatEntry]


@dataclass(frozen=True, slots=True)
class FormatModel:
    """The three-level format model of a registry, indexed by UUID."""

    versions: dict[str, FormatVersion]
    formats: dict[str, Format]
    groups: dict[str, FormatGroup]


def format_model(registry: Registry) -> FormatModel:
    return FormatModel(
        versions={f.uuid: f for f in registry.format_versions},
        formats={f.uuid: f for f in registry.formats},
        groups={g.uuid: g for g in registry.format_groups},
    )


def rule_format_entry(
    rule: FPRule, model: FormatModel, replaced_by: dict[str, list[str]]
) -> RuleFormatEntry:
    version = model.versions.get(rule.format)
    fmt = model.formats.get(version.format) if version else None
    group = model.groups.get(fmt.group) if fmt else None
    replacements = replaced_by.get(rule.uuid, [])
    return {
        "uuid": rule.uuid,
        "enabled": rule.enabled,
        "status": command_status(rule.enabled, bool(replacements)),
        "replaces": rule.replaces,
        "replaced_by": replacements,
        "format_version": format_version_label(version) if version else rule.format,
        "format_version_enabled": version.enabled if version else False,
        "pronom_id": version.pronom_id if version else "",
        "format": fmt.description if fmt else "",
        "group": group.description if group else "",
        "attempts": rule.count_attempts,
        "okay": rule.count_okay,
        "not_okay": rule.count_not_okay,
    }


def build_purposes(
    rules: list[FPRule], model: FormatModel, replaced_by: dict[str, list[str]]
) -> list[PurposeEntry]:
    """The rules of one command grouped by purpose, each purpose's formats
    sorted by group, format, version and PUID."""
    by_purpose: dict[str, list[RuleFormatEntry]] = {}
    for rule in rules:
        by_purpose.setdefault(rule.purpose, []).append(
            rule_format_entry(rule, model, replaced_by)
        )
    purposes: list[PurposeEntry] = []
    for purpose, entries in sorted(by_purpose.items()):
        entries.sort(
            key=lambda e: (e["group"], e["format"], e["format_version"], e["pronom_id"])
        )
        purposes.append(
            {
                "purpose": purpose,
                "label": PURPOSE_LABELS.get(purpose, purpose),
                "enabled": sum(1 for e in entries if e["enabled"]),
                "disabled": sum(1 for e in entries if not e["enabled"]),
                "formats": entries,
            }
        )
    return purposes


class Codecs(TypedDict):
    decoders: list[str]
    encoders: list[str]


class CommandEntry(TypedDict):
    id: int
    uuid: str
    description: str
    usage: str
    usage_key: str
    usage_label: str
    enabled: bool
    status: Status
    script_type: str
    script_type_label: str
    command: str
    output_location: str | None
    output_format: str
    output_format_puid: str
    tool: str
    tool_version: str
    tool_enabled: bool | None
    tool_uuid: str | None
    verification_command: str
    verification_command_uuid: str | None
    event_detail_command: str
    event_detail_command_uuid: str | None
    replaces: str | None
    replaces_description: str
    replaced_by: list[str]
    replaced_by_descriptions: list[str]
    executables: list[str]
    shell_utilities: list[str]
    imports: list[str]
    placeholders: list[str]
    rules_enabled: int
    rules_disabled: int
    purposes: list[PurposeEntry]
    codecs: Codecs
    lastmodified: str


def replaced_by_index(
    rows: Sequence[FPCommand | IDCommand | FPRule],
) -> dict[str, list[str]]:
    """UUID of every replaced row to the UUIDs of the rows replacing it."""
    index: dict[str, list[str]] = {}
    for row in rows:
        if row.replaces:
            index.setdefault(row.replaces, []).append(row.uuid)
    return index


def build_commands(
    registry: Registry, executables: frozenset[str]
) -> list[CommandEntry]:
    commands = {c.uuid: c for c in registry.commands}
    tools = {t.uuid: t for t in registry.tools}
    model = format_model(registry)
    replaced_by = replaced_by_index(registry.commands)
    rule_replaced_by = replaced_by_index(registry.rules)
    rules_by_command: dict[str, list[FPRule]] = {}
    for rule in registry.rules:
        rules_by_command.setdefault(rule.command, []).append(rule)
    entries: list[CommandEntry] = []
    for command in registry.commands:
        tool = tools.get(command.tool) if command.tool else None
        output_format = (
            model.versions.get(command.output_format) if command.output_format else None
        )
        replacements = replaced_by.get(command.uuid, [])
        usage_key = command.command_usage.lower()
        rules = rules_by_command.get(command.uuid, [])
        rules_enabled = sum(1 for r in rules if r.enabled)
        called, shell_words = detect_calls(
            command.command, command.script_type, executables
        )
        imports = detect_imports(command.command, command.script_type)
        for module, executable in LIBRARY_EXECUTABLES.items():
            if module in imports and executable not in called:
                called.append(executable)
        codecs: Codecs = {"decoders": [], "encoders": []}
        if "ffmpeg" in called:
            decoders, encoders = ffmpeg_codecs(command.command)
            codecs = {"decoders": decoders, "encoders": encoders}
        entries.append(
            {
                "id": command.id,
                "uuid": command.uuid,
                "description": command.description,
                "usage": command.command_usage,
                "usage_key": usage_key,
                "usage_label": USAGE_LABELS.get(usage_key, command.command_usage),
                "enabled": command.enabled,
                "status": command_status(command.enabled, bool(replacements)),
                "script_type": command.script_type,
                "script_type_label": SCRIPT_TYPE_LABELS.get(
                    command.script_type, command.script_type
                ),
                "command": command.command,
                "output_location": command.output_location,
                "output_format": (
                    format_version_label(output_format) if output_format else ""
                ),
                "output_format_puid": output_format.pronom_id if output_format else "",
                "tool": tool.description if tool else "",
                "tool_version": tool.version if tool else "",
                "tool_enabled": tool.enabled if tool else None,
                "tool_uuid": command.tool,
                "verification_command": _description(
                    commands, command.verification_command
                ),
                "verification_command_uuid": command.verification_command,
                "event_detail_command": _description(
                    commands, command.event_detail_command
                ),
                "event_detail_command_uuid": command.event_detail_command,
                "replaces": command.replaces,
                "replaces_description": _description(commands, command.replaces),
                "replaced_by": replacements,
                "replaced_by_descriptions": [
                    _description(commands, uuid) for uuid in replacements
                ],
                "executables": called,
                "shell_utilities": shell_words,
                "imports": imports,
                "placeholders": detect_placeholders(
                    command.command, command.output_location
                ),
                "rules_enabled": rules_enabled,
                "rules_disabled": len(rules) - rules_enabled,
                "purposes": build_purposes(rules, model, rule_replaced_by),
                "codecs": codecs,
                "lastmodified": command.lastmodified,
            }
        )
    entries.sort(key=lambda e: (e["usage_key"], e["description"].lower(), e["id"]))
    return entries


def _description(
    rows: dict[str, FPCommand] | dict[str, IDCommand], uuid: str | None
) -> str:
    if not uuid:
        return ""
    row = rows.get(uuid)
    return row.description if row else uuid


# --- derived tables -----------------------------------------------------------


class ToolEntry(TypedDict):
    uuid: str
    description: str
    version: str
    enabled: bool
    slug: str
    commands_enabled: list[str]
    commands_disabled: list[str]


class IDCommandEntry(TypedDict):
    id: int
    uuid: str
    description: str
    config: str
    config_label: str
    enabled: bool
    status: Status
    script_type: str
    script_type_label: str
    script: str
    tool: str
    tool_version: str
    tool_enabled: bool | None
    tool_uuid: str | None
    executables: list[str]
    imports: list[str]
    replaces: str | None
    replaces_description: str
    replaced_by: list[str]
    idrules_enabled: int
    idrules_disabled: int
    lastmodified: str


class ExecutableEntry(TypedDict):
    name: str
    path: str | None
    version: str | None
    verdict: Verdict
    commands: list[str]
    command_uuids: list[str]
    id_commands: list[str]
    id_command_uuids: list[str]


def build_tools(
    tools: Sequence[FPTool | IDTool],
    commands: Sequence[CommandEntry | IDCommandEntry],
) -> list[ToolEntry]:
    """The tool rows with the commands pointing at each, enabled and disabled
    apart; serves the FPR tools and the identification tools alike."""
    by_tool: dict[str, list[CommandEntry | IDCommandEntry]] = {}
    for command in commands:
        if command["tool_uuid"]:
            by_tool.setdefault(command["tool_uuid"], []).append(command)
    entries: list[ToolEntry] = []
    for tool in tools:
        linked = by_tool.get(tool.uuid, [])
        entries.append(
            {
                "uuid": tool.uuid,
                "description": tool.description,
                "version": tool.version,
                "enabled": tool.enabled,
                "slug": tool.slug,
                "commands_enabled": [c["description"] for c in linked if c["enabled"]],
                "commands_disabled": [
                    c["description"] for c in linked if not c["enabled"]
                ],
            }
        )
    entries.sort(key=lambda e: (e["description"].lower(), e["version"]))
    return entries


def build_id_commands(
    registry: Registry, executables: frozenset[str]
) -> list[IDCommandEntry]:
    commands = {c.uuid: c for c in registry.id_commands}
    tools = {t.uuid: t for t in registry.id_tools}
    replaced_by = replaced_by_index(registry.id_commands)
    rule_counts = Counter((r.command, r.enabled) for r in registry.id_rules)
    entries: list[IDCommandEntry] = []
    for command in registry.id_commands:
        tool = tools.get(command.tool) if command.tool else None
        called, _ = detect_calls(command.script, command.script_type, executables)
        replacements = replaced_by.get(command.uuid, [])
        entries.append(
            {
                "id": command.id,
                "uuid": command.uuid,
                "description": command.description,
                "config": command.config,
                "config_label": ID_CONFIG_LABELS.get(command.config, command.config),
                "enabled": command.enabled,
                "status": command_status(command.enabled, bool(replacements)),
                "script_type": command.script_type,
                "script_type_label": SCRIPT_TYPE_LABELS.get(
                    command.script_type, command.script_type
                ),
                "script": command.script,
                "tool": tool.description if tool else "",
                "tool_version": tool.version if tool else "",
                "tool_enabled": tool.enabled if tool else None,
                "tool_uuid": command.tool,
                "executables": called,
                "imports": detect_imports(command.script, command.script_type),
                "replaces": command.replaces,
                "replaces_description": _description(commands, command.replaces),
                "replaced_by": replacements,
                "idrules_enabled": rule_counts[(command.uuid, True)],
                "idrules_disabled": rule_counts[(command.uuid, False)],
                "lastmodified": command.lastmodified,
            }
        )
    entries.sort(key=lambda e: (not e["enabled"], e["description"].lower(), e["id"]))
    return entries


def build_executables(
    commands: list[CommandEntry],
    id_commands: list[IDCommandEntry],
    probe: Probe | None,
) -> list[ExecutableEntry]:
    """Every executable the probe resolved or an enabled command calls, with
    the probe's verdict and the enabled commands calling it."""
    names: set[str] = set(probe.executables) if probe else set()
    names.update(b for c in commands if c["enabled"] for b in c["executables"])
    names.update(b for c in id_commands if c["enabled"] for b in c["executables"])
    entries: list[ExecutableEntry] = []
    for name in sorted(names):
        probed = probe.executables.get(name) if probe else None
        verdict: Verdict
        if probed is None:
            verdict = "not probed"
        elif probed["path"]:
            verdict = "present"
        else:
            verdict = "missing"
        callers = [c for c in commands if c["enabled"] and name in c["executables"]]
        id_callers = [
            c for c in id_commands if c["enabled"] and name in c["executables"]
        ]
        entries.append(
            {
                "name": name,
                "path": probed["path"] if probed else None,
                "version": probed["version"] if probed else None,
                "verdict": verdict,
                "commands": [c["description"] for c in callers],
                "command_uuids": [c["uuid"] for c in callers],
                "id_commands": [c["description"] for c in id_callers],
                "id_command_uuids": [c["uuid"] for c in id_callers],
            }
        )
    return entries


# --- checks -------------------------------------------------------------------


class Observation(TypedDict):
    check: str
    message: str
    subjects: list[str]


@dataclass(frozen=True, slots=True)
class CheckContext:
    """What the checks look at: the derived tables and the probe."""

    commands: list[CommandEntry]
    executables: list[ExecutableEntry]
    probe: Probe | None
    known_executables: frozenset[str]


def _label(command: CommandEntry) -> str:
    return f'"{command["description"]}" ({command["usage_key"]}, {command["uuid"][:8]})'


def check_enabled_superseded(context: CheckContext) -> list[Observation]:
    observations: list[Observation] = [
        {
            "check": "enabled-superseded",
            "message": (
                f"{_label(c)} is enabled although a newer row replaces it "
                f"({', '.join(c['replaced_by_descriptions'])}); "
                "the dashboard list hides it"
            ),
            "subjects": [c["uuid"]],
        }
        for c in context.commands
        if c["status"] == "enabled-superseded"
    ]
    for command in context.commands:
        for purpose in command["purposes"]:
            for entry in purpose["formats"]:
                if entry["status"] == "enabled-superseded":
                    observations.append(
                        {
                            "check": "enabled-superseded",
                            "message": (
                                f"the {purpose['purpose']} rule selecting "
                                f"{_label(command)} for "
                                f'"{entry["format_version"]}" ({entry["pronom_id"]}) '
                                "is enabled although a newer rule replaces it"
                            ),
                            "subjects": [entry["uuid"], command["uuid"]],
                        }
                    )
    return observations


def check_usage_case(context: CheckContext) -> list[Observation]:
    return [
        {
            "check": "usage-case",
            "message": (
                f"{_label(c)} stores its usage as {c['usage']!r} instead of "
                f"{c['usage_key']!r}; the dashboard's per-usage list filters on "
                "the exact value"
            ),
            "subjects": [c["uuid"]],
        }
        for c in context.commands
        if c["usage"] != c["usage_key"] and c["usage_key"] in USAGE_LABELS
    ]


def check_disabled_tool(context: CheckContext) -> list[Observation]:
    return [
        {
            "check": "disabled-tool",
            "message": (
                f"{_label(c)} is enabled but its tool row "
                f'"{c["tool"]} {c["tool_version"]}" is disabled; the flag only '
                "limits the choices in the command form"
            ),
            "subjects": [c["uuid"], c["tool_uuid"] or ""],
        }
        for c in context.commands
        if c["enabled"] and c["tool_enabled"] is False
    ]


def check_disabled_format_version(context: CheckContext) -> list[Observation]:
    observations: list[Observation] = []
    for command in context.commands:
        for purpose in command["purposes"]:
            for entry in purpose["formats"]:
                if entry["enabled"] and not entry["format_version_enabled"]:
                    observations.append(
                        {
                            "check": "disabled-format-version",
                            "message": (
                                f"the enabled {purpose['purpose']} rule selecting "
                                f"{_label(command)} for "
                                f'"{entry["format_version"]}" ({entry["pronom_id"]}) '
                                "points at a format version that is "
                                "disabled or missing"
                            ),
                            "subjects": [entry["uuid"], command["uuid"]],
                        }
                    )
    return observations


def check_no_enabled_rules(context: CheckContext) -> list[Observation]:
    return [
        {
            "check": "no-enabled-rules",
            "message": f"{_label(c)} is enabled but no enabled rule selects it",
            "subjects": [c["uuid"]],
        }
        for c in context.commands
        if c["enabled"]
        and c["usage_key"] not in LINKED_USAGES
        and c["rules_enabled"] == 0
    ]


def check_unreferenced_linked_command(context: CheckContext) -> list[Observation]:
    referenced: set[str] = set()
    for command in context.commands:
        if command["enabled"]:
            for uuid in (
                command["verification_command_uuid"],
                command["event_detail_command_uuid"],
            ):
                if uuid:
                    referenced.add(uuid)
    return [
        {
            "check": "unreferenced-linked-command",
            "message": f"{_label(c)} is enabled but no enabled command links to it",
            "subjects": [c["uuid"]],
        }
        for c in context.commands
        if c["enabled"]
        and c["usage_key"] in LINKED_USAGES
        and c["uuid"] not in referenced
    ]


def check_sudo_or_absolute_path(context: CheckContext) -> list[Observation]:
    observations: list[Observation] = []
    for command in context.commands:
        if not command["enabled"] or command["script_type"] not in SHELL_SCRIPT_TYPES:
            continue
        reasons: list[str] = []
        if "sudo" in command["shell_utilities"]:
            reasons.append("runs programs through sudo")
        paths = absolute_program_paths(command["command"], context.known_executables)
        if paths:
            reasons.append(f"starts {', '.join(paths)} by absolute path")
        if reasons:
            observations.append(
                {
                    "check": "sudo-or-absolute-path",
                    "message": f"{_label(command)} {' and '.join(reasons)}",
                    "subjects": [command["uuid"]],
                }
            )
    return observations


def _called_by_enabled(executable: ExecutableEntry) -> bool:
    return bool(executable["commands"] or executable["id_commands"])


def check_executable_missing(context: CheckContext) -> list[Observation]:
    observations: list[Observation] = []
    for e in context.executables:
        if e["verdict"] != "missing" or not _called_by_enabled(e):
            continue
        callers = e["commands"] + [f"{c} (identification)" for c in e["id_commands"]]
        observations.append(
            {
                "check": "executable-missing",
                "message": (
                    f"{e['name']} is absent from the host's service PATH but "
                    f"{', '.join(callers)} "
                    f"{'calls' if len(callers) == 1 else 'call'} it"
                ),
                "subjects": [e["name"], *e["command_uuids"], *e["id_command_uuids"]],
            }
        )
    return observations


def check_ffmpeg_codecs(context: CheckContext) -> list[Observation]:
    probe = context.probe
    if probe is None or probe.decoders is None or probe.encoders is None:
        return []
    observations: list[Observation] = []
    for command in context.commands:
        if not command["enabled"] or "ffmpeg" not in command["executables"]:
            continue
        decoders = command["codecs"]["decoders"]
        encoders = command["codecs"]["encoders"]
        observations.extend(
            _missing_codec(command, "forced-decoder-missing", "forces", "decoder", d)
            for d in decoders
            if d not in probe.decoders
        )
        observations.extend(
            _missing_codec(command, "encoder-missing", "names", "encoder", e)
            for e in encoders
            if e not in probe.encoders
        )
    return observations


def _missing_codec(
    command: CommandEntry, check: str, verb: str, role: str, codec: str
) -> Observation:
    return {
        "check": check,
        "message": (
            f"{_label(command)} {verb} the ffmpeg {role} {codec}, which this "
            "host's ffmpeg does not have"
        ),
        "subjects": [codec, command["uuid"]],
    }


def check_probe_options(context: CheckContext) -> list[Observation]:
    """The option checks the probe ran, reported against the enabled commands
    that use the option."""
    if context.probe is None:
        return []
    observations: list[Observation] = []
    for check in context.probe.checks:
        if check["result"] not in ("deprecated", "rejected"):
            continue
        if check["id"] == "inkscape-option":
            program, option = "inkscape", check["subject"]
            users = [
                c
                for c in context.commands
                if c["enabled"]
                and "inkscape" in c["executables"]
                and option in program_options(c["command"], "inkscape")
            ]
        elif check["id"] == "convert-deprecation":
            program, option = "convert", "convert"
            users = [
                c
                for c in context.commands
                if c["enabled"] and "convert" in c["executables"]
            ]
        else:
            continue
        if not users:
            continue
        verdict = "deprecated" if check["result"] == "deprecated" else "rejected"
        observations.append(
            {
                "check": f"option-{verdict}",
                "message": (
                    f"{program} on this host reports {option} as {verdict} "
                    f"({check['output'] or 'no output'}); used by "
                    f"{', '.join(_label(c) for c in users)}"
                ),
                "subjects": [program, option, *(c["uuid"] for c in users)],
            }
        )
    return observations


CHECKS = (
    check_enabled_superseded,
    check_usage_case,
    check_disabled_tool,
    check_disabled_format_version,
    check_no_enabled_rules,
    check_unreferenced_linked_command,
    check_sudo_or_absolute_path,
    check_executable_missing,
    check_ffmpeg_codecs,
    check_probe_options,
)


def run_checks(context: CheckContext) -> list[Observation]:
    observations: list[Observation] = []
    for check in CHECKS:
        observations.extend(check(context))
    return observations


# --- the inventory document ---------------------------------------------------


class Summary(TypedDict):
    commands: int
    commands_enabled: int
    commands_by_usage: dict[str, int]
    tools: int
    tools_enabled: int
    rules: int
    rules_enabled: int
    rules_by_purpose: dict[str, int]
    id_commands: int
    id_commands_enabled: int
    id_tools: int
    id_rules: int
    id_rules_enabled: int
    format_versions: int
    format_versions_enabled: int
    formats: int
    format_groups: int
    executables: list[str]
    executables_missing: list[str]
    observations: dict[str, int]


class MigrationSummary(TypedDict):
    applied: int
    latest: str
    latest_applied_at: str


class MigrationEntry(TypedDict):
    name: str
    applied: str


class Provenance(TypedDict):
    reporter_version: str
    contract_version: int
    dumped_at: str
    raw_dump: str
    probe: str | None
    probed_at: str | None
    host: str | None
    user: str | None
    os: str | None
    path: str | None
    interpreter: ProbeInterpreter | None
    archivematica: ProbeArchivematica | None
    migrations: MigrationSummary | None
    meta: dict[str, object] | None
    view: str


class Inventory(TypedDict):
    provenance: Provenance
    summary: Summary
    commands: list[CommandEntry]
    executables: list[ExecutableEntry]
    tools: list[ToolEntry]
    id_commands: list[IDCommandEntry]
    id_tools: list[ToolEntry]
    observations: list[Observation]
    migrations: list[MigrationEntry]
    probe: dict[str, object] | None


def build_summary(
    registry: Registry,
    commands: list[CommandEntry],
    executables: list[ExecutableEntry],
    observations: list[Observation],
) -> Summary:
    return {
        "commands": len(registry.commands),
        "commands_enabled": sum(1 for c in registry.commands if c.enabled),
        "commands_by_usage": dict(
            sorted(
                Counter(
                    c.command_usage.lower() for c in registry.commands if c.enabled
                ).items()
            )
        ),
        "tools": len(registry.tools),
        "tools_enabled": sum(1 for t in registry.tools if t.enabled),
        "rules": len(registry.rules),
        "rules_enabled": sum(1 for r in registry.rules if r.enabled),
        "rules_by_purpose": dict(
            sorted(Counter(r.purpose for r in registry.rules if r.enabled).items())
        ),
        "id_commands": len(registry.id_commands),
        "id_commands_enabled": sum(1 for c in registry.id_commands if c.enabled),
        "id_tools": len(registry.id_tools),
        "id_rules": len(registry.id_rules),
        "id_rules_enabled": sum(1 for r in registry.id_rules if r.enabled),
        "format_versions": len(registry.format_versions),
        "format_versions_enabled": sum(
            1 for f in registry.format_versions if f.enabled
        ),
        "formats": len(registry.formats),
        "format_groups": len(registry.format_groups),
        "executables": sorted(
            {b for c in commands if c["enabled"] for b in c["executables"]}
        ),
        "executables_missing": [
            e["name"]
            for e in executables
            if e["verdict"] == "missing" and _called_by_enabled(e)
        ],
        "observations": dict(sorted(Counter(o["check"] for o in observations).items())),
    }


def build_provenance(
    inputs: BuildInputs,
) -> Provenance:
    probe = inputs.probe
    return {
        "reporter_version": REPORTER_VERSION,
        "contract_version": CONTRACT_VERSION,
        "dumped_at": inputs.registry.dumped_at,
        "raw_dump": inputs.raw_path.name,
        "probe": inputs.probe_path.name if inputs.probe_path else None,
        "probed_at": probe.probed_at if probe else None,
        "host": probe.host if probe else None,
        "user": probe.user if probe else None,
        "os": probe.os if probe else None,
        "path": probe.path if probe else None,
        "interpreter": probe.interpreter if probe else None,
        "archivematica": probe.archivematica if probe else None,
        "migrations": summarize_migrations(inputs.registry.migrations),
        "meta": inputs.meta,
        "view": "all rows" if inputs.include_all else "enabled rows only",
    }


def summarize_migrations(migrations: list[Migration]) -> MigrationSummary | None:
    """The applied fpr migrations as a count and the latest by name; None for
    a dump taken before the migrations were part of it."""
    if not migrations:
        return None
    latest = max(migrations, key=lambda m: m.name)
    return {
        "applied": len(migrations),
        "latest": latest.name,
        "latest_applied_at": latest.applied,
    }


@dataclass(frozen=True, slots=True)
class BuildInputs:
    """What a build works from: the loaded files, their paths for the
    provenance, and whether disabled and superseded rows are shown."""

    registry: Registry
    probe: Probe | None
    raw_path: Path
    probe_path: Path | None
    meta: dict[str, object] | None
    include_all: bool


def enabled_rules_only(command: CommandEntry) -> CommandEntry:
    """The command with the disabled rules left out of its format lists; the
    per-purpose counts still say how many there were."""
    purposes: list[PurposeEntry] = [
        {
            "purpose": purpose["purpose"],
            "label": purpose["label"],
            "enabled": purpose["enabled"],
            "disabled": purpose["disabled"],
            "formats": [f for f in purpose["formats"] if f["enabled"]],
        }
        for purpose in command["purposes"]
    ]
    return {**command, "purposes": purposes}


def build_inventory(inputs: BuildInputs) -> Inventory:
    registry, probe = inputs.registry, inputs.probe
    known = KNOWN_EXECUTABLES | frozenset(probe.executables if probe else ())
    commands = build_commands(registry, known)
    id_commands = build_id_commands(registry, known)
    executables = build_executables(commands, id_commands, probe)
    observations = run_checks(CheckContext(commands, executables, probe, known))
    if inputs.include_all:
        shown = commands
    else:
        shown = [enabled_rules_only(c) for c in commands if c["enabled"]]
    return {
        "provenance": build_provenance(inputs),
        "summary": build_summary(registry, commands, executables, observations),
        "commands": shown,
        "executables": executables,
        "tools": build_tools(registry.tools, commands),
        "id_commands": [c for c in id_commands if inputs.include_all or c["enabled"]],
        "id_tools": build_tools(registry.id_tools, id_commands),
        "observations": observations,
        "migrations": [
            {"name": m.name, "applied": m.applied}
            for m in sorted(registry.migrations, key=lambda m: m.name)
        ],
        "probe": probe.document if probe else None,
    }


# --- Markdown -----------------------------------------------------------------


FORMATS_SHOWN = 12
USAGE_ORDER = tuple(USAGE_LABELS)

PLACEHOLDER_INTRO = (
    "Placeholders are `%name%` tokens. For `command` and `bashScript` rows the "
    "client script substitutes them in the text before running it; for "
    "`pythonScript` and `as_is` rows the text is written to a file and run with "
    "arguments instead, and which arguments depends on the client script."
)
FILE_PLACEHOLDERS = [
    ["`%fileUUID%`", "UUID of the file"],
    [
        "`%fileFullName%`, `%inputFile%`, `%currentLocation%`, `%relativeLocation%`",
        "Absolute current path of the file (the four are synonyms in the file context)",
    ],
    ["`%originalLocation%`", "Original path of the file when it entered the transfer"],
    ["`%fileDirectory%`", "Directory of the current path"],
    ["`%fileName%`", "Base name without extension"],
    [
        "`%fileExtension%`, `%fileExtensionWithDot%`",
        "Extension without and with the leading dot",
    ],
    ["`%fileGrpUse%`", "File group use (original, preservation, access...)"],
    [
        "`%SIPUUID%`, `%SIPName%`, `%SIPDirectory%`, `%currentPath%`, "
        "`%SIPDirectoryBasename%`",
        "The owning SIP or transfer and its directory",
    ],
    [
        "`%SIPLogsDirectory%`, `%SIPObjectsDirectory%`",
        "`logs/` and `objects/` inside that directory, with a trailing slash",
    ],
    [
        "`%transferDirectory%`",
        "Transfer directory (only when the dict is built in the transfer context)",
    ],
    [
        "`%tmpDirectory%`, `%processingDirectory%`, `%watchDirectoryPath%`, "
        "`%rejectedDirectory%`",
        "Shared-directory paths from the MCPClient configuration",
    ],
]
SCRIPT_PLACEHOLDERS = [
    [
        "`%outputDirectory%`",
        "normalize.py, extract_contents.py",
        "Where the derivative goes: the file's directory (preservation), "
        "`DIP/objects/` (access), `thumbnails/` (thumbnail), or the extraction "
        "target directory",
    ],
    [
        "`%prefix%`, `%postfix%`",
        "normalize.py",
        "Access: prefix is the file UUID plus a dash. Preservation: postfix is a "
        "dash plus the task UUID. Thumbnail: postfix is the file UUID. Otherwise "
        "empty",
    ],
    [
        "`%outputFileName%`, `%outputFilePath%`",
        "normalize.py",
        "prefix + file name + postfix, and that joined to the output directory "
        "(no extension)",
    ],
    [
        "`%outputLocation%`",
        "transcoder.Command",
        "The command's `output_location` after substitution; available to its "
        "verification and event detail commands",
    ],
]
CLIENT_SCRIPTS = [
    [
        "normalize.py",
        "Rules with purpose access, preservation or thumbnail for the file's "
        "format version; falls back to default_access or default_thumbnail",
        "Text substituted with the full dict",
        "Every placeholder becomes a GNU long option, e.g. "
        "`--output-location=...`, `--file-uuid=...`",
        "On exit 0 runs the verification command, then the event detail command "
        "(both through `transcoder.Command`); the event detail output goes into "
        "the PREMIS event. A failed access or thumbnail command without an output "
        "file falls back to the default rule",
    ],
    [
        "characterize_file.py",
        "Rules with purpose characterization for the format version, else "
        "default_characterization",
        "Text substituted",
        "GNU long options",
        "Standard output is kept as the file's characterization output",
    ],
    [
        "extract_contents.py",
        "The enabled command of an enabled extract rule for the format version",
        "Only `%inputFile%` and `%outputDirectory%` are substituted",
        "Two positional arguments: the package path and the target directory",
        "No verification or event detail command runs",
    ],
    [
        "transcribe_file.py",
        "Rules with purpose transcription (originals only)",
        "Text substituted",
        "GNU long options",
        "The substituted `output_location` is recorded in the transcription event",
    ],
    [
        "validate_file.py",
        "Rules with purpose validation for the format version, else default_validation",
        "Text substituted",
        "One positional argument: the file path",
        "Standard output must be JSON with `eventOutcomeInformation` and "
        "`eventOutcomeDetailNote`; exit code 0 means the command ran, the outcome "
        "decides pass or fail",
    ],
    [
        "policy_check.py",
        "Rules with purpose policy_check for the format version, else "
        "default_policy_check",
        "Text substituted",
        "One positional argument: the file path",
        "Same JSON contract; the policy file is copied into the unit's `logs/`",
    ],
    [
        "identify_file_format.py",
        "The enabled `IDCommand`",
        "One positional argument: the file path",
        "One positional argument: the file path",
        "Output is matched to a format version by PUID, or through the ID rules "
        "for the other configurations",
    ],
]
SCRIPT_TYPES_NOTE = (
    "`executeOrRun()` runs a `command` row as one command line split with "
    "`shlex`; a `bashScript` or `pythonScript` row is written to a temporary "
    "file with the matching shebang and executed with the arguments appended; "
    "an `as_is` row is written and executed without a shebang."
)


def render_placeholder_notes() -> list[str]:
    """The static section on placeholders and client-script conventions."""
    return [
        "## Placeholders and client-script conventions",
        "",
        PLACEHOLDER_INTRO,
        "",
        "### Placeholders set for a file (ReplacementDict.frommodel())",
        "",
        *md_table(["Placeholder", "Value"], FILE_PLACEHOLDERS),
        "",
        "### Placeholders added by particular client scripts",
        "",
        *md_table(["Placeholder", "Set by", "Value"], SCRIPT_PLACEHOLDERS),
        "",
        "### How each client script invokes its commands",
        "",
        *md_table(
            [
                "Client script",
                "Selects",
                "command / bashScript",
                "pythonScript / as_is",
                "Afterwards",
            ],
            CLIENT_SCRIPTS,
        ),
        "",
        "### Script types",
        "",
        SCRIPT_TYPES_NOTE,
        "",
    ]


def md_cell(text: str) -> str:
    """Text safe inside a table cell."""
    return text.replace("\r", "").replace("\n", " ").replace("|", "\\|")


def md_code(text: str) -> str:
    if not text:
        return ""
    longest = max((len(m.group(0)) for m in re.finditer(r"`+", text)), default=0)
    fence = "`" * (longest + 1)
    return f"{fence}{text}{fence}"


def md_fenced(text: str) -> list[str]:
    longest = max((len(m.group(0)) for m in re.finditer(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return [fence, text.rstrip("\n"), fence]


def md_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join(" --- " for _ in headers) + "|",
    ]
    lines.extend("| " + " | ".join(md_cell(c) for c in row) + " |" for row in rows)
    return lines


def _enabled_word(enabled: bool | None) -> str:
    if enabled is None:
        return "unknown"
    return "enabled" if enabled else "disabled"


def render_title(provenance: Provenance) -> list[str]:
    meta = provenance["meta"]
    host = (
        (_optional_text(meta, "inventory_hostname") if meta else None)
        or provenance["host"]
        or "unknown host"
    )
    lines = [
        f"# FPR inventory: {host}",
        "",
        f"- Registry dumped at {provenance['dumped_at'] or 'an unknown time'} "
        f"({provenance['raw_dump']}); view: {provenance['view']}.",
    ]
    if provenance["probe"]:
        lines.append(
            f"- Probe of {provenance['host'] or 'an unknown host'} "
            f"({provenance['os'] or 'unknown OS'}) at "
            f"{provenance['probed_at'] or 'an unknown time'} as "
            f"{provenance['user'] or 'an unknown user'} ({provenance['probe']})."
        )
        lines.append(f"- Service PATH: {md_code(provenance['path'] or '')}.")
        interpreter = provenance["interpreter"]
        if interpreter:
            lines.append(
                f"- Interpreter: {interpreter['version'] or interpreter['name']} "
                f"at {md_code(interpreter['path'] or '')}."
            )
        evidence = provenance["archivematica"]
        if evidence and evidence["version"]:
            version = (
                f"{evidence['version']} ({evidence['source']}, "
                f"{md_code(evidence['location'] or '')})"
            )
        else:
            version = "none found on disk"
        checkout = evidence["checkout"] if evidence else None
        if checkout and checkout["description"]:
            source = (
                f"checkout {checkout['branch'] or 'detached'} at "
                f"{checkout['description']} ({md_code(checkout['path'] or '')})"
            )
        else:
            source = "no source checkout found"
        lines.append(f"- Archivematica version evidence: {version}; {source}.")
    else:
        lines.append("- No probe: executables were not resolved on the host.")
    migrations = provenance["migrations"]
    if migrations:
        lines.append(
            f"- Registry migrations: {migrations['applied']} applied, latest "
            f"{migrations['latest']} ({migrations['latest_applied_at']})."
        )
    else:
        lines.append("- Registry migrations: not in this dump.")
    if meta:
        facts = meta.get("os")
        os_facts = _object(facts, "meta.os") if isinstance(facts, dict) else {}
        run_id = _optional_text(meta, "run_id") or "unknown"
        role_version = _optional_text(meta, "role_version") or "unknown"
        lines.append(
            f"- Inventory run {run_id} by the archivematica_fpr role "
            f"({role_version}) on {_optional_text(meta, 'fqdn') or host}, "
            f"{_text(os_facts, 'distribution')} {_text(os_facts, 'version')}."
        )
    lines.append(
        f"- Rendered by fpr_inventory.py {provenance['reporter_version']} "
        f"(contract version {provenance['contract_version']})."
    )
    lines.append("")
    return lines


def render_summary(summary: Summary) -> list[str]:
    by_usage = ", ".join(f"{k} {v}" for k, v in summary["commands_by_usage"].items())
    by_purpose = ", ".join(f"{k} {v}" for k, v in summary["rules_by_purpose"].items())
    observations = sum(summary["observations"].values())
    return [
        "## Summary",
        "",
        f"- Commands: {summary['commands']}, {summary['commands_enabled']} enabled "
        f"({by_usage or 'none'}).",
        f"- Tools: {summary['tools']}, {summary['tools_enabled']} enabled.",
        f"- Rules: {summary['rules']}, {summary['rules_enabled']} enabled "
        f"({by_purpose or 'none'}).",
        f"- Identification: {summary['id_commands']} commands "
        f"({summary['id_commands_enabled']} enabled), {summary['id_tools']} tools, "
        f"{summary['id_rules']} rules ({summary['id_rules_enabled']} enabled).",
        f"- Formats: {summary['format_versions']} format versions "
        f"({summary['format_versions_enabled']} enabled), {summary['formats']} "
        f"formats, {summary['format_groups']} groups.",
        f"- Executables called by enabled commands: "
        f"{', '.join(summary['executables']) or 'none'}.",
        f"- Missing on the host: "
        f"{', '.join(summary['executables_missing']) or 'none'}.",
        f"- Observations: {observations}.",
        "",
    ]


def _format_lines(purpose: PurposeEntry) -> list[str]:
    by_group: dict[str, list[str]] = {}
    for entry in purpose["formats"]:
        name = entry["format_version"]
        if entry["pronom_id"]:
            name = f"{name} ({entry['pronom_id']})"
        if entry["status"] != "enabled":
            name += f" [rule {entry['status']}]"
        if not entry["format_version_enabled"]:
            name += " [format version disabled]"
        by_group.setdefault(entry["group"] or "no group", []).append(name)
    lines: list[str] = []
    for group, names in by_group.items():
        shown = ", ".join(names[:FORMATS_SHOWN])
        if len(names) > FORMATS_SHOWN:
            shown += f" (+{len(names) - FORMATS_SHOWN} more)"
        lines.append(f"    - {group} ({len(names)}): {shown}")
    return lines


def render_command(command: CommandEntry) -> list[str]:
    tool = f'"{command["tool"]} {command["tool_version"]}"'.replace(' "', '"')
    lines = [
        f"### {command['description']}",
        "",
        f"- Status: {command['status']}; script type: "
        f"{command['script_type_label']}; tool: {tool} "
        f"({_enabled_word(command['tool_enabled'])}); UUID {md_code(command['uuid'])}; "
        f"last modified {command['lastmodified'] or 'unknown'}.",
        "",
        *md_fenced(command["command"]),
        "",
    ]
    if command["output_location"]:
        lines.append(f"- Output location: {md_code(command['output_location'])}.")
    if command["output_format"]:
        puid = (
            f" ({command['output_format_puid']})"
            if command["output_format_puid"]
            else ""
        )
        lines.append(f"- Output format: {command['output_format']}{puid}.")
    if command["verification_command"]:
        lines.append(f"- Verification command: {command['verification_command']}.")
    if command["event_detail_command"]:
        lines.append(f"- Event detail command: {command['event_detail_command']}.")
    if command["replaces"]:
        lines.append(
            f"- Replaces: {command['replaces_description']} "
            f"({md_code(command['replaces'])})."
        )
    if command["replaced_by"]:
        replacements = ", ".join(
            f"{description} ({md_code(uuid)})"
            for description, uuid in zip(
                command["replaced_by_descriptions"], command["replaced_by"], strict=True
            )
        )
        lines.append(f"- Replaced by: {replacements}.")
    placeholders = ", ".join(md_code(f"%{name}%") for name in command["placeholders"])
    lines.append(f"- Placeholders: {placeholders or 'none'}.")
    runs = [f"executables: {', '.join(command['executables']) or 'none'}"]
    if command["shell_utilities"]:
        runs.append(f"shell utilities: {', '.join(command['shell_utilities'])}")
    if command["imports"]:
        runs.append(f"Python imports: {', '.join(command['imports'])}")
    lines.append(f"- Runs: {'; '.join(runs)}.")
    codecs = command["codecs"]
    if codecs["decoders"] or codecs["encoders"]:
        parts = []
        if codecs["decoders"]:
            parts.append(f"forces decoder {', '.join(codecs['decoders'])}")
        if codecs["encoders"]:
            parts.append(f"names encoder {', '.join(codecs['encoders'])}")
        lines.append(f"- ffmpeg: {'; '.join(parts)}.")
    if command["purposes"]:
        lines.append("- Rules:")
        for purpose in command["purposes"]:
            lines.append(
                f"  - {purpose['purpose']}: {purpose['enabled']} enabled "
                f"({purpose['disabled']} disabled)"
            )
            lines.extend(_format_lines(purpose))
    else:
        lines.append("- Rules: none.")
    lines.append("")
    return lines


def render_commands(commands: list[CommandEntry]) -> list[str]:
    by_usage: dict[str, list[CommandEntry]] = {}
    for command in commands:
        by_usage.setdefault(command["usage_key"], []).append(command)
    order = [u for u in USAGE_ORDER if u in by_usage]
    order.extend(sorted(u for u in by_usage if u not in USAGE_LABELS))
    lines: list[str] = []
    for usage in order:
        rows = by_usage[usage]
        lines.append(f"## {USAGE_LABELS.get(usage, usage)} commands")
        lines.append("")
        lines.extend(
            md_table(
                ["Command", "Tool", "Type", "Runs", "Output", "Rules", "Status"],
                [
                    [
                        c["description"],
                        f"{c['tool']} {c['tool_version']}".strip(),
                        c["script_type_label"],
                        " ".join(c["executables"]),
                        f"{c['output_format']} {c['output_format_puid']}".strip(),
                        (
                            f"{c['rules_enabled']} enabled "
                            f"({c['rules_disabled']} disabled)"
                            if c["rules_enabled"] or c["rules_disabled"]
                            else ""
                        ),
                        c["status"],
                    ]
                    for c in rows
                ],
            )
        )
        lines.append("")
        for command in rows:
            lines.extend(render_command(command))
    return lines


def render_executables(executables: list[ExecutableEntry]) -> list[str]:
    rows = [
        [
            e["name"],
            e["verdict"],
            e["path"] or "",
            e["version"] or "",
            "; ".join(e["commands"] + e["id_commands"]),
        ]
        for e in executables
    ]
    return [
        "## Executables",
        "",
        "Resolved through the MCPClient service PATH; the version is the first "
        "line the program prints for its version flag.",
        "",
        *md_table(["Executable", "Verdict", "Path", "Version", "Called by"], rows),
        "",
    ]


def tool_table(tools: list[ToolEntry]) -> list[str]:
    return md_table(
        ["Tool", "Recorded version", "Row", "Enabled commands", "Disabled commands"],
        [
            [
                t["description"],
                t["version"],
                _enabled_word(t["enabled"]),
                "; ".join(t["commands_enabled"]),
                "; ".join(t["commands_disabled"]),
            ]
            for t in tools
        ],
    )


def render_tools(tools: list[ToolEntry]) -> list[str]:
    return [
        "## Tools",
        "",
        "The recorded version is a label from when the row was created, not what "
        "the host has; a disabled tool only disappears from the command form.",
        "",
        *tool_table(tools),
        "",
    ]


def render_id_commands(
    id_commands: list[IDCommandEntry], id_tools: list[ToolEntry]
) -> list[str]:
    rows = [
        [
            c["description"],
            c["config_label"],
            c["script_type_label"],
            f"{c['tool']} {c['tool_version']}".strip(),
            c["status"],
            f"{c['idrules_enabled']} enabled ({c['idrules_disabled']} disabled)",
        ]
        for c in id_commands
    ]
    lines = [
        "## Identification commands",
        "",
        *md_table(["Command", "Config", "Type", "Tool", "Status", "Rules"], rows),
        "",
    ]
    for command in id_commands:
        if command["enabled"]:
            lines.append(f"### {command['description']}")
            lines.append("")
            lines.append(
                f"- Status: {command['status']}; runs: "
                f"{', '.join(command['executables']) or 'no known executable'}; "
                f"UUID {md_code(command['uuid'])}."
            )
            lines.append("")
            lines.extend(md_fenced(command["script"]))
            lines.append("")
    lines.append("### Identification tools")
    lines.append("")
    lines.extend(tool_table(id_tools))
    lines.append("")
    return lines


def render_observations(observations: list[Observation]) -> list[str]:
    lines = ["## Observations", ""]
    if not observations:
        lines.append("None.")
    for number, observation in enumerate(observations, start=1):
        lines.append(
            f"{number}. {md_code(observation['check'])}: {observation['message']}."
        )
    lines.append("")
    return lines


def render_markdown(inventory: Inventory) -> str:
    lines: list[str] = []
    lines.extend(render_title(inventory["provenance"]))
    lines.extend(render_summary(inventory["summary"]))
    lines.extend(render_commands(inventory["commands"]))
    lines.extend(render_executables(inventory["executables"]))
    lines.extend(render_tools(inventory["tools"]))
    lines.extend(render_id_commands(inventory["id_commands"], inventory["id_tools"]))
    lines.extend(render_placeholder_notes())
    lines.extend(render_observations(inventory["observations"]))
    return "\n".join(lines).rstrip("\n") + "\n"


# --- commands -----------------------------------------------------------------


def command_build(args: argparse.Namespace) -> int:
    raw_path = Path(args.raw)
    probe_path = Path(args.probe) if args.probe else None
    meta_path = Path(args.meta) if args.meta else None
    out_dir = Path(args.out)
    registry = load_registry(raw_path)
    probe = load_probe(probe_path) if probe_path else None
    meta = _object(_load_json(meta_path), str(meta_path)) if meta_path else None
    inventory = build_inventory(
        BuildInputs(registry, probe, raw_path, probe_path, meta, bool(args.all))
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    if args.format in ("json", "both"):
        target = out_dir / "fpr-inventory.json"
        with target.open("w", encoding="utf-8") as handle:
            json.dump(inventory, handle, indent=1, ensure_ascii=False)
            handle.write("\n")
        written.append(target)
    if args.format in ("markdown", "both"):
        target = out_dir / "fpr-inventory.md"
        target.write_text(render_markdown(inventory), encoding="utf-8")
        written.append(target)
    for target in written:
        print(target)
    return 0


def command_collect(args: argparse.Namespace) -> int:
    """Runs the dump statement through PyMySQL and writes the raw dump, for use
    without Ansible against a reachable database."""
    try:
        import pymysql
    except ImportError as error:
        raise ReporterError(
            "collect needs PyMySQL; run it as "
            "`uv run --with pymysql --script fpr_inventory.py collect ...`"
        ) from error
    url = urllib.parse.urlsplit(args.database_url)
    database = url.path.strip("/")
    if url.scheme != "mysql" or not url.hostname or not database:
        raise ReporterError(
            "--database-url must look like mysql://user:password@host:port/database"
        )
    statement_path = Path(__file__).with_name("dump_fpr.sql")
    try:
        statement = statement_path.read_text(encoding="utf-8")
    except OSError as error:
        raise ReporterError(f"{statement_path}: {error.strerror}") from error
    statements = [s.strip() for s in _without_comments(statement).split(";")]
    statements = [s for s in statements if s]
    try:
        connection = pymysql.connect(
            host=url.hostname,
            port=url.port or 3306,
            user=urllib.parse.unquote(url.username or ""),
            password=urllib.parse.unquote(url.password or ""),
            database=database,
            charset="utf8mb4",
        )
    except pymysql.Error as error:
        raise ReporterError(f"cannot connect to {url.hostname}: {error}") from error
    try:
        with connection.cursor() as cursor:
            for single in statements:
                cursor.execute(single)
            row = cursor.fetchone()
    except pymysql.Error as error:
        raise ReporterError(f"the dump statement failed: {error}") from error
    finally:
        connection.close()
    if row is None:
        raise ReporterError("the dump statement returned no row")
    document = row[0]
    if isinstance(document, bytes):
        document = document.decode("utf-8")
    if not isinstance(document, str):
        raise ReporterError("the dump statement returned no JSON text")
    out_path = Path(args.out)
    out_path.write_text(document.rstrip("\n") + "\n", encoding="utf-8")
    print(out_path)
    return 0


def _without_comments(sql: str) -> str:
    """The SQL text without its full-line ``--`` comments."""
    return "\n".join(
        line for line in sql.splitlines() if not line.lstrip().startswith("--")
    )


def command_compare(args: argparse.Namespace) -> int:
    raise ReporterError(
        "compare: not implemented; reserved for the baseline comparison"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fpr_inventory.py",
        description="Inventory of an Archivematica Format Policy Registry.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser(
        "build", help="turn a raw dump and a probe into the inventory"
    )
    build.add_argument("--raw", required=True, help="the raw registry dump")
    build.add_argument("--probe", help="the executable probe of the MCPClient host")
    build.add_argument("--meta", help="the metadata file the role writes")
    build.add_argument("--out", required=True, help="directory for the output files")
    build.add_argument(
        "--all",
        action="store_true",
        help="include disabled and superseded rows",
    )
    build.add_argument(
        "--format",
        choices=("json", "markdown", "both"),
        default="both",
        help="which outputs to write (default: both)",
    )
    build.set_defaults(func=command_build)

    collect = subparsers.add_parser(
        "collect", help="dump a reachable database without Ansible (needs PyMySQL)"
    )
    collect.add_argument(
        "--database-url",
        required=True,
        help="mysql://user:password@host:port/database",
    )
    collect.add_argument("--out", required=True, help="the raw dump to write")
    collect.set_defaults(func=command_collect)

    compare = subparsers.add_parser(
        "compare", help="compare a dump with a baseline (reserved)"
    )
    compare.add_argument("--raw", required=True, help="the raw registry dump")
    compare.add_argument("--baseline", required=True, help="the pristine dump")
    compare.set_defaults(func=command_compare)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        status: int = args.func(args)
    except ReporterError as error:
        print(f"fpr_inventory: {error}", file=sys.stderr)
        return 2
    return status


if __name__ == "__main__":
    sys.exit(main())
