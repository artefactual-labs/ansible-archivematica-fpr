"""Tests of the FPR inventory reporter at its command-line seam.

The reporter is run as a subprocess on the fixture files: the raw dump of the
qa/1.x development stack's registry at migration 0055 (``hack-fpr.json``), the
probe of that stack's MCPClient image (``probe-ubuntu-26.04.json``) and a
metadata file in the shape the role writes. Expected values are independent of
the reporter: row counts and facts checked against the dump by hand, and the
per-command table of ``expected-commands.csv``, derived from the same dump
before the reporter existed.
"""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path
from typing import TypedDict

import pytest

ROLE_DIR = Path(__file__).resolve().parent.parent
REPORTER = ROLE_DIR / "files" / "fpr_inventory.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
RAW_DUMP = FIXTURES / "hack-fpr.json"
PROBE = FIXTURES / "probe-ubuntu-26.04.json"
META = FIXTURES / "meta-hack.json"
EXPECTED_COMMANDS = FIXTURES / "expected-commands.csv"


class RuleFormat(TypedDict):
    enabled: bool
    status: str
    format_version: str
    format_version_enabled: bool
    pronom_id: str
    format: str
    group: str


class Purpose(TypedDict):
    purpose: str
    enabled: int
    disabled: int
    formats: list[RuleFormat]


class Command(TypedDict):
    uuid: str
    description: str
    usage: str
    status: str
    script_type: str
    executables: list[str]
    shell_utilities: list[str]
    imports: list[str]
    placeholders: list[str]
    rules_enabled: int
    rules_disabled: int
    purposes: list[Purpose]
    codecs: dict[str, list[str]]


class Executable(TypedDict):
    name: str
    path: str | None
    version: str | None
    verdict: str
    commands: list[str]


class IDCommand(TypedDict):
    uuid: str
    description: str
    config: str
    enabled: bool
    status: str
    script_type: str
    tool: str
    tool_version: str
    executables: list[str]
    idrules_enabled: int
    idrules_disabled: int


class Observation(TypedDict):
    check: str
    message: str
    subjects: list[str]


class Provenance(TypedDict):
    archivematica: dict[str, object] | None
    migrations: dict[str, object] | None


class Inventory(TypedDict):
    provenance: Provenance
    summary: dict[str, object]
    commands: list[Command]
    executables: list[Executable]
    id_commands: list[IDCommand]
    observations: list[Observation]


class BuildResult(TypedDict):
    inventory: Inventory
    markdown: str
    out_dir: Path


def run_build(out_dir: Path, *extra: str, probe: Path = PROBE) -> BuildResult:
    subprocess.run(
        [
            sys.executable,
            str(REPORTER),
            "build",
            "--raw",
            str(RAW_DUMP),
            "--probe",
            str(probe),
            "--meta",
            str(META),
            "--out",
            str(out_dir),
            *extra,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    inventory: Inventory = json.loads((out_dir / "fpr-inventory.json").read_text())
    return {
        "inventory": inventory,
        "markdown": (out_dir / "fpr-inventory.md").read_text(),
        "out_dir": out_dir,
    }


@pytest.fixture(scope="session")
def full_build(tmp_path_factory: pytest.TempPathFactory) -> BuildResult:
    """A build with ``--all``: disabled and superseded rows included."""
    return run_build(tmp_path_factory.mktemp("full"), "--all")


@pytest.fixture(scope="session")
def default_build(tmp_path_factory: pytest.TempPathFactory) -> BuildResult:
    """A build without ``--all``: enabled rows only."""
    return run_build(tmp_path_factory.mktemp("default"))


def test_build_counts_every_registry_table(full_build: BuildResult) -> None:
    summary = full_build["inventory"]["summary"]
    tables = (
        "commands",
        "tools",
        "rules",
        "format_versions",
        "formats",
        "format_groups",
        "id_commands",
        "id_tools",
        "id_rules",
    )
    assert {table: summary[table] for table in tables} == {
        "commands": 56,
        "tools": 30,
        "rules": 873,
        "format_versions": 2621,
        "formats": 1688,
        "format_groups": 31,
        "id_commands": 25,
        "id_tools": 5,
        "id_rules": 1336,
    }


def commands_by_uuid(build: BuildResult) -> dict[str, Command]:
    return {command["uuid"]: command for command in build["inventory"]["commands"]}


# The old fiwalk row and the old "Transcoding to plain svg with inkscape" row are
# enabled although a newer row replaces them; the four "Validate using JHOVE"
# rows are the disabled history behind the per-module JHOVE commands.
STATUS_CASES = [
    ("928ce834-8830-44cc-a282-4ba1271f7842", "fiwalk", "enabled-superseded"),
    ("fcd033ec-8e29-48e8-99b9-6dbccd8f9c6e", "fiwalk", "enabled"),
    (
        "64c450b4-135c-46d1-a9aa-3f9b15671677",
        "Transcoding to plain svg with inkscape",
        "enabled-superseded",
    ),
    (
        "cf8b44c8-ba16-44cd-8b47-cb29d52fbac4",
        "Transcoding to plain svg with inkscape",
        "enabled",
    ),
    ("fe9e3046-29fb-4cc6-b71c-db50b0aac127", "Validate using JHOVE", "superseded"),
    ("11036e14-78d9-4449-8360-e2da394279ad", "Validate using JHOVE", "superseded"),
    ("4cac3267-453e-4456-9306-6d098dacb70d", "Validate using JHOVE", "superseded"),
    ("cb335c49-e6ce-445f-a774-494a6f2300c6", "Validate using JHOVE", "disabled"),
    (
        "a74cbffb-281b-49ee-8e96-fbac2ab54785",
        "Transcoding to pdf with ps2pdf",
        "disabled",
    ),
]


@pytest.mark.parametrize(("uuid", "description", "status"), STATUS_CASES)
def test_build_classifies_command_rows(
    full_build: BuildResult, uuid: str, description: str, status: str
) -> None:
    command = commands_by_uuid(full_build)[uuid]
    assert (command["description"], command["status"]) == (description, status)


class ExpectedCommand(TypedDict):
    uuid: str
    description: str
    usage: str
    status: str
    script_type: str
    executables: list[str]
    shell_utilities: list[str]
    python_imports: list[str]
    placeholders: list[str]
    rules_enabled: int
    rules_disabled: int
    rule_purposes: dict[str, int]


def expected_commands() -> list[ExpectedCommand]:
    """The per-command table, one row per command of the fixture dump."""
    with EXPECTED_COMMANDS.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    return [
        {
            "uuid": row["uuid"],
            "description": row["description"],
            "usage": row["usage"],
            "status": row["status"],
            "script_type": row["script_type"],
            "executables": row["executables"].split(),
            "shell_utilities": row["shell_utilities"].split(),
            "python_imports": row["python_imports"].split(),
            "placeholders": row["placeholders"].split(),
            "rules_enabled": int(row["rules_enabled"]),
            "rules_disabled": int(row["rules_disabled"]),
            "rule_purposes": {
                purpose: int(count)
                for purpose, count in (
                    item.split("=") for item in row["rule_purposes"].split("; ") if item
                )
            },
        }
        for row in rows
    ]


EXPECTED = expected_commands()
EXPECTED_IDS = [f"{row['description']} {row['uuid'][:8]}" for row in EXPECTED]


@pytest.mark.parametrize("expected", EXPECTED, ids=EXPECTED_IDS)
def test_build_detects_what_each_command_runs(
    full_build: BuildResult, expected: ExpectedCommand
) -> None:
    command = commands_by_uuid(full_build)[expected["uuid"]]
    assert (
        command["executables"],
        command["shell_utilities"],
        command["imports"],
    ) == (
        expected["executables"],
        expected["shell_utilities"],
        expected["python_imports"],
    )


@pytest.mark.parametrize("expected", EXPECTED, ids=EXPECTED_IDS)
def test_build_lists_placeholders_and_rules_per_purpose(
    full_build: BuildResult, expected: ExpectedCommand
) -> None:
    command = commands_by_uuid(full_build)[expected["uuid"]]
    enabled_by_purpose = {
        purpose["purpose"]: purpose["enabled"]
        for purpose in command["purposes"]
        if purpose["enabled"]
    }
    assert (
        command["placeholders"],
        command["rules_enabled"],
        command["rules_disabled"],
        enabled_by_purpose,
    ) == (
        expected["placeholders"],
        expected["rules_enabled"],
        expected["rules_disabled"],
        expected["rule_purposes"],
    )


def rule_formats(
    command: Command,
) -> list[tuple[str, int, int, list[tuple[str, str, str, str, bool, bool]]]]:
    return [
        (
            purpose["purpose"],
            purpose["enabled"],
            purpose["disabled"],
            [
                (
                    entry["format_version"],
                    entry["pronom_id"],
                    entry["format"],
                    entry["group"],
                    entry["enabled"],
                    entry["format_version_enabled"],
                )
                for entry in purpose["formats"]
            ],
        )
        for purpose in command["purposes"]
    ]


def test_build_lists_the_formats_a_rule_covers(full_build: BuildResult) -> None:
    """The JP2 preservation rule is the only one selecting the ffmpeg TIFF
    command."""
    command = commands_by_uuid(full_build)["66ba8388-2cd2-44a7-9d30-5d194d47c75b"]
    assert rule_formats(command) == [
        (
            "preservation",
            1,
            0,
            [
                (
                    "JP2 (JPEG 2000 part 1)",
                    "x-fmt/392",
                    "JP2 (JPEG 2000 part 1)",
                    "Image (Raster)",
                    True,
                    True,
                )
            ],
        )
    ]


def test_build_flags_rules_on_disabled_format_versions(
    full_build: BuildResult,
) -> None:
    """The fiwalk characterization rule for AFF points at a disabled format
    version."""
    command = commands_by_uuid(full_build)["fcd033ec-8e29-48e8-99b9-6dbccd8f9c6e"]
    flagged = [
        entry
        for purpose in command["purposes"]
        for entry in purpose["formats"]
        if not entry["format_version_enabled"]
    ]
    assert [
        (entry["format_version"], entry["pronom_id"], entry["group"], entry["enabled"])
        for entry in flagged
    ] == [("Advanced Forensic Format AFFv3", "archivematica-fmt/5", "Disk Image", True)]


def test_build_lists_the_executables_enabled_commands_call(
    full_build: BuildResult,
) -> None:
    assert full_build["inventory"]["summary"]["executables"] == [
        "7z",
        "convert",
        "exiftool",
        "ffmpeg",
        "ffprobe",
        "fiwalk",
        "gs",
        "inkscape",
        "jhove",
        "mediaconch",
        "mediainfo",
        "ps2pdf",
        "readpst",
        "tesseract",
        "tsk_recover",
        "unrar",
        "unrar-nonfree",
    ]


def executables_by_name(build: BuildResult) -> dict[str, Executable]:
    return {entry["name"]: entry for entry in build["inventory"]["executables"]}


def test_build_reports_the_probe_verdict_per_executable(
    full_build: BuildResult,
) -> None:
    """unrar-nonfree is absent from the image and named by one enabled command;
    magick is present and called by none; ffmpeg is present at 8.0.1."""
    executables = executables_by_name(full_build)
    missing = executables["unrar-nonfree"]
    magick = executables["magick"]
    ffmpeg = executables["ffmpeg"]
    assert (
        (missing["verdict"], missing["path"], missing["commands"]),
        (magick["verdict"], magick["path"], magick["commands"]),
        (ffmpeg["verdict"], ffmpeg["path"], ffmpeg["commands"]),
    ) == (
        ("missing", None, ["Get event detail text for unrar extraction"]),
        ("present", "/usr/bin/magick", []),
        (
            "present",
            "/usr/bin/ffmpeg",
            [
                "Get event detail text for ffmpeg extraction",
                "Transcoding to mkv with ffmpeg",
                "Transcoding to mp3 with ffmpeg",
                "Transcoding to mp4 with ffmpeg",
                "Transcoding to tif with ffmpeg",
                "Transcoding to wav with ffmpeg",
            ],
        ),
    )
    assert (ffmpeg["version"] or "").startswith("ffmpeg version 8.0.1")


def test_build_lists_the_identification_command_in_force(
    full_build: BuildResult,
) -> None:
    enabled = [c for c in full_build["inventory"]["id_commands"] if c["enabled"]]
    assert [
        (
            c["description"],
            c["config"],
            c["script_type"],
            c["tool"],
            c["tool_version"],
            c["executables"],
            c["idrules_enabled"],
            c["idrules_disabled"],
        )
        for c in enabled
    ] == [
        (
            "Identify using Siegfried 1.11.5",
            "PUID",
            "pythonScript",
            "Siegfried",
            "1.11.5",
            ["sf"],
            0,
            0,
        )
    ]


# One observation per finding: the two enabled-superseded rows, the three usage
# values with a capital letter, the twenty-six enabled commands on disabled tool
# rows, the two enabled rules on the disabled AFF format version, the three
# enabled commands no enabled rule selects, the readpst event detail nothing
# links, the two svg commands that use sudo or an absolute path, the missing
# unrar-nonfree, the libopenjpeg decoder FFmpeg 8 lacks and the three
# deprecated Inkscape options.
EXPECTED_OBSERVATIONS = {
    "enabled-superseded": 2,
    "usage-case": 3,
    "disabled-tool": 26,
    "disabled-format-version": 2,
    "no-enabled-rules": 3,
    "unreferenced-linked-command": 1,
    "sudo-or-absolute-path": 2,
    "executable-missing": 1,
    "forced-decoder-missing": 1,
    "option-deprecated": 3,
}


def test_report_has_the_fixed_sections_in_order(full_build: BuildResult) -> None:
    markdown = full_build["markdown"]
    headings = [line[3:] for line in markdown.splitlines() if line.startswith("## ")]
    assert (markdown.splitlines()[0], headings) == (
        "# FPR inventory: am-dev-stack",
        [
            "Summary",
            "Characterization commands",
            "Event detail commands",
            "Extraction commands",
            "Normalization commands",
            "Transcription commands",
            "Validation commands",
            "Verification commands",
            "Executables",
            "Tools",
            "Identification commands",
            "Placeholders and client-script conventions",
            "Observations",
        ],
    )


def test_report_shows_command_texts_and_findings(full_build: BuildResult) -> None:
    markdown = full_build["markdown"]
    expected_fragments = [
        'exiftool -X "%fileFullName%"',
        'ffmpeg -vcodec libopenjpeg -i "%fileFullName%"',
        "| unrar-nonfree | missing |",
        "forces the ffmpeg decoder libopenjpeg",
        "Identify using Siegfried 1.11.5",
        "%outputDirectory%",
    ]
    assert [
        fragment for fragment in expected_fragments if fragment not in markdown
    ] == []


def test_build_yields_the_observations_of_every_check(full_build: BuildResult) -> None:
    observations = full_build["inventory"]["observations"]
    counts: dict[str, int] = {}
    for observation in observations:
        counts[observation["check"]] = counts.get(observation["check"], 0) + 1
    assert counts == EXPECTED_OBSERVATIONS


def test_build_names_what_each_observation_concerns(full_build: BuildResult) -> None:
    observations = full_build["inventory"]["observations"]
    bulk = {"disabled-tool", "disabled-format-version", "no-enabled-rules"}
    assert sorted(
        (o["check"], *o["subjects"]) for o in observations if o["check"] not in bulk
    ) == sorted(
        [
            ("enabled-superseded", "928ce834-8830-44cc-a282-4ba1271f7842"),
            ("enabled-superseded", "64c450b4-135c-46d1-a9aa-3f9b15671677"),
            ("usage-case", "928ce834-8830-44cc-a282-4ba1271f7842"),
            ("usage-case", "fcd033ec-8e29-48e8-99b9-6dbccd8f9c6e"),
            ("usage-case", "b8864e2c-87a0-4aec-a8cf-a747a073be28"),
            ("unreferenced-linked-command", "42e7fdc3-42c6-4715-b79a-e900df916cab"),
            ("sudo-or-absolute-path", "64c450b4-135c-46d1-a9aa-3f9b15671677"),
            ("sudo-or-absolute-path", "cf8b44c8-ba16-44cd-8b47-cb29d52fbac4"),
            (
                "executable-missing",
                "unrar-nonfree",
                "c072c3c8-ba0e-480b-b0ee-2f27c6c0653e",
            ),
            (
                "forced-decoder-missing",
                "libopenjpeg",
                "66ba8388-2cd2-44a7-9d30-5d194d47c75b",
            ),
            (
                "option-deprecated",
                "inkscape",
                "-z",
                "d91f0637-b2f6-4492-ac1b-23c155102a3e",
            ),
            (
                "option-deprecated",
                "inkscape",
                "--export-pdf",
                "d91f0637-b2f6-4492-ac1b-23c155102a3e",
            ),
            (
                "option-deprecated",
                "inkscape",
                "--export-plain-svg",
                "64c450b4-135c-46d1-a9aa-3f9b15671677",
                "cf8b44c8-ba16-44cd-8b47-cb29d52fbac4",
            ),
        ]
    )


def test_default_build_shows_enabled_rows_and_rules_only(
    default_build: BuildResult, full_build: BuildResult
) -> None:
    """Without --all the superseded and disabled rows disappear, and so do the
    disabled rules' formats; the counts still say what was left out."""
    commands = default_build["inventory"]["commands"]
    tif_convert = commands_by_uuid(default_build)[
        "a34ddc9b-c922-4bb6-8037-bbe713332175"
    ]
    full_tif_convert = commands_by_uuid(full_build)[
        "a34ddc9b-c922-4bb6-8037-bbe713332175"
    ]
    assert (
        len(commands),
        sorted({c["status"] for c in commands}),
        [len(p["formats"]) for p in tif_convert["purposes"]],
        [len(p["formats"]) for p in full_tif_convert["purposes"]],
        [(p["enabled"], p["disabled"]) for p in tif_convert["purposes"]],
        len(default_build["inventory"]["id_commands"]),
    ) == (43, ["enabled", "enabled-superseded"], [50], [68], [(50, 18)], 1)


def test_build_without_a_probe_reports_executables_as_not_probed(
    tmp_path: Path,
) -> None:
    subprocess.run(
        [
            sys.executable,
            str(REPORTER),
            "build",
            "--raw",
            str(RAW_DUMP),
            "--out",
            str(tmp_path),
            "--format",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    inventory: Inventory = json.loads((tmp_path / "fpr-inventory.json").read_text())
    assert (
        sorted({e["verdict"] for e in inventory["executables"]}),
        [e["name"] for e in inventory["executables"] if e["name"] == "ffmpeg"],
        (tmp_path / "fpr-inventory.md").exists(),
        inventory["summary"]["executables_missing"],
    ) == (["not probed"], ["ffmpeg"], False, [])


def reporter(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(REPORTER), *args], capture_output=True, text=True
    )


def test_build_refuses_a_file_that_is_not_a_dump(tmp_path: Path) -> None:
    result = reporter(
        "build", "--raw", str(PROBE), "--out", str(tmp_path), "--format", "json"
    )
    assert (result.returncode, "not a registry dump" in result.stderr) == (2, True)


def test_compare_is_reserved(tmp_path: Path) -> None:
    result = reporter("compare", "--raw", str(RAW_DUMP), "--baseline", str(RAW_DUMP))
    assert (result.returncode, "not implemented" in result.stderr) == (2, True)


def test_collect_names_the_missing_driver(tmp_path: Path) -> None:
    """The test environment has no PyMySQL, so collect must say how to get it."""
    result = reporter(
        "collect",
        "--database-url",
        "mysql://user:secret@127.0.0.1:1/MCP",
        "--out",
        str(tmp_path / "fpr-raw.json"),
    )
    assert (result.returncode, "uv run --with pymysql" in result.stderr) == (2, True)


def test_build_classifies_rule_rows(full_build: BuildResult) -> None:
    """Twenty-seven rules of the fixture were replaced by a newer rule and are
    all disabled; no replaced rule is still enabled."""
    statuses: dict[str, int] = {}
    for command in full_build["inventory"]["commands"]:
        for purpose in command["purposes"]:
            for entry in purpose["formats"]:
                statuses[entry["status"]] = statuses.get(entry["status"], 0) + 1
    assert statuses == {"enabled": 802, "disabled": 44, "superseded": 27}


def probe_variant(tmp_path: Path, **changes: object) -> Path:
    """The fixture probe with top-level keys replaced."""
    document = json.loads(PROBE.read_text())
    for key, value in changes.items():
        document[key] = value
    path = tmp_path / "probe.json"
    path.write_text(json.dumps(document))
    return path


def test_build_reports_a_missing_identification_executable(tmp_path: Path) -> None:
    """Siegfried's sf is the one executable whose absence stops every file."""
    document = json.loads(PROBE.read_text())
    executables = dict(document["executables"])
    executables["sf"] = {"path": None, "version": None}
    probe = probe_variant(tmp_path, executables=executables)
    build = run_build(tmp_path / "out", probe=probe)
    missing = [
        o
        for o in build["inventory"]["observations"]
        if o["check"] == "executable-missing"
    ]
    assert (
        build["inventory"]["summary"]["executables_missing"],
        sorted(o["subjects"][0] for o in missing),
        [o["subjects"] for o in missing if o["subjects"][0] == "sf"],
    ) == (
        ["sf", "unrar-nonfree"],
        ["sf", "unrar-nonfree"],
        [["sf", "3634f5e1-65ba-423f-82c2-c0ec34560e9d"]],
    )


def test_build_treats_a_failed_codec_listing_as_unknown(tmp_path: Path) -> None:
    """When the probe could not list ffmpeg's codecs, no decoder or encoder is
    reported missing; the forced libopenjpeg decoder is simply unknown."""
    probe = probe_variant(
        tmp_path,
        ffmpeg={"decoders": None, "encoders": None, "error": "timed out"},
    )
    build = run_build(tmp_path / "out", probe=probe)
    checks = {o["check"] for o in build["inventory"]["observations"]}
    assert (
        "forced-decoder-missing" in checks,
        "encoder-missing" in checks,
        build["inventory"]["executables"] != [],
    ) == (False, False, True)


# The ffmpeg commands of the fixture: the TIFF one forces the libopenjpeg
# decoder, the MKV one assembles its line through shell variables and names
# two encoders, the MP4 one names libx264 and the MP3 one names nothing.
CODEC_CASES = [
    ("66ba8388-2cd2-44a7-9d30-5d194d47c75b", ["libopenjpeg"], []),
    ("2d991241-e352-4a77-b104-e7e82fb119c4", [], ["ffv1", "pcm_s16le"]),
    ("bb5a6da2-4b89-4f8c-96e3-ca36c55d3337", [], ["libx264"]),
    ("520f976f-476d-4ec6-be1c-7c448c5c074d", [], []),
]


@pytest.mark.parametrize(("uuid", "decoders", "encoders"), CODEC_CASES)
def test_build_reads_the_codecs_an_ffmpeg_command_names(
    full_build: BuildResult, uuid: str, decoders: list[str], encoders: list[str]
) -> None:
    command = commands_by_uuid(full_build)[uuid]
    assert command["codecs"] == {"decoders": decoders, "encoders": encoders}


class SyntheticCase(TypedDict):
    name: str
    script_type: str
    command: str
    executables: list[str]
    shell_utilities: list[str]
    decoders: list[str]
    encoders: list[str]


# Command texts that the detection must read the way a shell or Python would:
# an assignment prefix, a quoted sentence, a command string after -c, a Python
# list argument, and codec options with stream specifiers or a later command.
SYNTHETIC_CASES: list[SyntheticCase] = [
    {
        "name": "assignment prefix",
        "script_type": "command",
        "command": "LC_ALL=C ffmpeg -i input output",
        "executables": ["ffmpeg"],
        "shell_utilities": [],
        "decoders": [],
        "encoders": [],
    },
    {
        "name": "quoted sentence",
        "script_type": "bashScript",
        "command": 'echo "ffmpeg is absent"',
        "executables": [],
        "shell_utilities": ["echo"],
        "decoders": [],
        "encoders": [],
    },
    {
        "name": "command string after -c",
        "script_type": "bashScript",
        "command": (
            "flock -x /tmp/l -c "
            "'libreoffice --headless --convert-to pdf \"%fileFullName%\"'"
        ),
        "executables": ["libreoffice"],
        "shell_utilities": ["flock"],
        "decoders": [],
        "encoders": [],
    },
    {
        "name": "command string after sh -c",
        "script_type": "bashScript",
        "command": 'timeout 20 sh -c "convert a b"',
        "executables": ["convert"],
        "shell_utilities": [],
        "decoders": [],
        "encoders": [],
    },
    {
        "name": "python list argument",
        "script_type": "pythonScript",
        "command": (
            "import subprocess\n"
            'print("convert failed")\n'
            'subprocess.call(["convert", "a", "b"])\n'
        ),
        "executables": ["convert"],
        "shell_utilities": [],
        "decoders": [],
        "encoders": [],
    },
    {
        "name": "codec options with stream specifiers",
        "script_type": "command",
        "command": "ffmpeg -codec:v h264 -i in -c:v:0 ffv1 -c:a:0 pcm_s16le out",
        "executables": ["ffmpeg"],
        "shell_utilities": [],
        "decoders": ["h264"],
        "encoders": ["ffv1", "pcm_s16le"],
    },
    {
        "name": "a later command is not an encoder",
        "script_type": "bashScript",
        "command": 'ffmpeg -i in out\npython3 -c "print(1)"\n',
        "executables": ["ffmpeg", "python3"],
        "shell_utilities": [],
        "decoders": [],
        "encoders": [],
    },
]


def synthetic_dump(path: Path, cases: list[SyntheticCase]) -> None:
    """A registry with one enabled normalization command per case and nothing
    else."""
    commands = [
        {
            "id": index,
            "uuid": f"00000000-0000-4000-8000-{index:012d}",
            "replaces": None,
            "enabled": 1,
            "lastmodified": "2026-10-16 00:00:00.000000",
            "description": case["name"],
            "command": case["command"],
            "script_type": case["script_type"],
            "output_location": None,
            "command_usage": "normalization",
            "tool": None,
            "output_format": None,
            "verification_command": None,
            "event_detail_command": None,
        }
        for index, case in enumerate(cases, start=1)
    ]
    document = {
        "dumped_at": "2026-10-16 00:00:00.000000",
        "fpcommand": commands,
        "fptool": [],
        "fprule": [],
        "formatversion": [],
        "format": [],
        "formatgroup": [],
        "idcommand": [],
        "idtool": [],
        "idrule": [],
    }
    path.write_text(json.dumps(document))


@pytest.fixture(scope="session")
def synthetic_build(tmp_path_factory: pytest.TempPathFactory) -> BuildResult:
    root = tmp_path_factory.mktemp("synthetic")
    raw = root / "fpr-raw.json"
    synthetic_dump(raw, SYNTHETIC_CASES)
    out = root / "out"
    subprocess.run(
        [
            sys.executable,
            str(REPORTER),
            "build",
            "--raw",
            str(raw),
            "--probe",
            str(PROBE),
            "--out",
            str(out),
            "--format",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    inventory: Inventory = json.loads((out / "fpr-inventory.json").read_text())
    return {"inventory": inventory, "markdown": "", "out_dir": out}


@pytest.mark.parametrize(
    "case", SYNTHETIC_CASES, ids=[case["name"] for case in SYNTHETIC_CASES]
)
def test_build_reads_command_texts_like_a_shell(
    synthetic_build: BuildResult, case: SyntheticCase
) -> None:
    command = {c["description"]: c for c in synthetic_build["inventory"]["commands"]}[
        case["name"]
    ]
    assert (
        command["executables"],
        command["shell_utilities"],
        command["codecs"],
    ) == (
        case["executables"],
        case["shell_utilities"],
        {"decoders": case["decoders"], "encoders": case["encoders"]},
    )


def test_synthetic_commands_yield_no_codec_observations(
    synthetic_build: BuildResult,
) -> None:
    """Every codec the synthetic commands name exists on the fixture host, and
    the later python3 command must not be read as an encoder."""
    checks = [
        o["check"]
        for o in synthetic_build["inventory"]["observations"]
        if o["check"] in ("forced-decoder-missing", "encoder-missing")
    ]
    assert checks == []


def test_build_records_the_registry_migrations(full_build: BuildResult) -> None:
    """The fixture registry is the one migrations 0001 to 0055 create."""
    migrations = full_build["inventory"]["provenance"]["migrations"]
    assert migrations is not None
    assert (
        migrations["applied"],
        migrations["latest"],
        "latest 0055_update_idtools" in full_build["markdown"],
    ) == (55, "0055_update_idtools", True)


def test_report_names_the_source_checkout(full_build: BuildResult) -> None:
    """The probe found the stack's source checkout next to the package
    metadata; the report names its branch and description."""
    probe = json.loads(PROBE.read_text())
    checkout = probe["archivematica"]["checkout"]
    evidence = full_build["inventory"]["provenance"]["archivematica"]
    assert evidence is not None
    assert (
        evidence["checkout"],
        f"checkout {checkout['branch']} at {checkout['description']}"
        in full_build["markdown"],
    ) == (checkout, True)


def test_build_without_migrations_or_checkout_in_the_files(tmp_path: Path) -> None:
    """Older dumps and probes lack the two additions; the report says so."""
    document = json.loads(RAW_DUMP.read_text())
    del document["migrations"]
    raw = tmp_path / "fpr-raw.json"
    raw.write_text(json.dumps(document))
    probe_document = json.loads(PROBE.read_text())
    del probe_document["archivematica"]["checkout"]
    probe = tmp_path / "probe.json"
    probe.write_text(json.dumps(probe_document))
    out = tmp_path / "out"
    subprocess.run(
        [
            sys.executable,
            str(REPORTER),
            "build",
            "--raw",
            str(raw),
            "--probe",
            str(probe),
            "--out",
            str(out),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    inventory: Inventory = json.loads((out / "fpr-inventory.json").read_text())
    markdown = (out / "fpr-inventory.md").read_text()
    evidence = inventory["provenance"]["archivematica"]
    assert evidence is not None
    assert (
        inventory["provenance"]["migrations"],
        evidence["checkout"],
        "migrations: not in this dump" in markdown,
        "no source checkout found" in markdown,
    ) == (None, None, True, True)
