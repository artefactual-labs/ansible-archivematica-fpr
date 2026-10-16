#!/bin/bash
# Probe of the executables an Archivematica MCPClient host offers to its FPR
# commands, written as one JSON document on standard output.
#
# Usage: fpr-probe.sh [--env-file FILE] [--path PATH] [--timeout SECONDS] NAME...
#
#   --env-file FILE    source FILE, the MCPClient environment file, first
#   --path PATH        resolve executables through PATH instead of the
#                      environment's PATH
#   --timeout SECONDS  limit for each version or option call (default 20)
#   NAME...            the executables to resolve and version
#
# Every NAME is resolved through the PATH in effect, the way the MCPClient
# service resolves the programs its commands start, and asked for its version
# with the flag its program understands. ffmpeg also lists its decoders and
# encoders, inkscape and convert answer the option checks, and the interpreter,
# the operating system and the Archivematica version evidence found on disk are
# recorded alongside. The script writes no files and changes nothing.
#
# A missing executable is data in the document, not an error. The exit status is
# non-zero only when the arguments are wrong or the environment file cannot be
# read.
set -u

TIMEOUT=20
ENV_FILE=""
PROBE_PATH=""
NAMES=()

usage() {
  sed -n '2,20p' "$0" 2>/dev/null | sed 's/^# \{0,1\}//'
}

needs_value() {
  if [ "$2" -lt 2 ]; then
    echo "fpr-probe: $1 needs a value" >&2
    exit 2
  fi
}

while [ $# -gt 0 ]; do
  case "$1" in
    --env-file) needs_value "$1" $#; ENV_FILE="$2"; shift 2 ;;
    --env-file=*) ENV_FILE="${1#*=}"; shift ;;
    --path) needs_value "$1" $#; PROBE_PATH="$2"; shift 2 ;;
    --path=*) PROBE_PATH="${1#*=}"; shift ;;
    --timeout) needs_value "$1" $#; TIMEOUT="$2"; shift 2 ;;
    --timeout=*) TIMEOUT="${1#*=}"; shift ;;
    -h|--help) usage; exit 0 ;;
    --) shift; NAMES+=("$@"); break ;;
    -*) echo "fpr-probe: unknown option: $1" >&2; exit 2 ;;
    *) NAMES+=("$1"); shift ;;
  esac
done

case "$TIMEOUT" in
  ''|*[!0-9]*) echo "fpr-probe: --timeout needs a whole number of seconds" >&2; exit 2 ;;
esac

if [ -n "$ENV_FILE" ]; then
  if [ ! -r "$ENV_FILE" ]; then
    echo "fpr-probe: cannot read environment file $ENV_FILE" >&2
    exit 1
  fi
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi
if [ -n "$PROBE_PATH" ]; then
  export PATH="$PROBE_PATH"
fi

# --- JSON helpers -------------------------------------------------------------

# Keeps printable ASCII, tabs and newlines so that every value is valid JSON
# whatever the locale or the program's output encoding.
sanitize() {
  LC_ALL=C tr -d '\000-\010\013-\037\177' | LC_ALL=C tr -c '\011\012\040-\176' '?'
}

json_escape() {
  local s
  s=$(cat)
  s=${s//\\/\\\\}
  s=${s//\"/\\\"}
  s=${s//$'\t'/\\t}
  s=${s//$'\n'/\\n}
  printf '%s' "$s"
}

json_string() {
  printf '"%s"' "$(printf '%s' "$1" | sanitize | json_escape)"
}

json_string_or_null() {
  if [ -z "$1" ]; then
    printf 'null'
  else
    json_string "$1"
  fi
}

json_array() {
  local out="" item
  for item in "$@"; do
    out="$out${out:+, }$(json_string "$item")"
  done
  printf '[%s]' "$out"
}

# --- process helpers ----------------------------------------------------------

if type -P timeout >/dev/null 2>&1; then
  with_timeout() { timeout "$TIMEOUT" "$@"; }
else
  with_timeout() { "$@"; }
fi

# Runs a command with no input and a time limit; the combined output lands in
# OUT and the exit status in RC.
run() {
  OUT=$(with_timeout "$@" 2>&1 </dev/null)
  RC=$?
}

# The first line with content, cut to 160 characters.
first_line() {
  LC_ALL=C tr -d '\r' | LC_ALL=C awk 'NF { print substr($0, 1, 160); exit }'
}

# The first line matching the pattern, cut to 160 characters.
first_match() {
  LC_ALL=C tr -d '\r' | LC_ALL=C awk -v pattern="$1" \
    'tolower($0) ~ pattern { print substr($0, 1, 160); exit }'
}

# --- version flags ------------------------------------------------------------

version_args() {
  case "$1" in
    exiftool) printf '%s\n' -ver ;;
    ffmpeg|ffprobe) printf '%s\n' -version ;;
    mediainfo) printf '%s\n' --Version ;;
    fiwalk|readpst|tsk_recover) printf '%s\n' -V ;;
    7z|7za|7zr) printf '%s\n' i ;;
    convert|magick) printf '%s\n' -version ;;
    sf) printf '%s\n' -version ;;
    fido) printf '%s\n' -v ;;
    *) printf '%s\n' --version ;;
  esac
}

version_line() {
  case "$1" in
    mediainfo) first_match 'mediainfolib' ;;
    *) first_line ;;
  esac
}

probe_version() {
  local name="$1" path="$2" args line gs_path
  if [ "$name" = ps2pdf ]; then
    # A shell wrapper around gs with no version flag of its own.
    gs_path=$(type -P gs 2>/dev/null || true)
    if [ -z "$gs_path" ]; then
      return
    fi
    run "$gs_path" --version
    line=$(printf '%s\n' "$OUT" | first_line)
    if [ -n "$line" ]; then
      printf 'wrapper for gs %s' "$line"
    fi
    return
  fi
  args=$(version_args "$name")
  run "$path" "$args"
  line=$(printf '%s\n' "$OUT" | version_line "$name")
  if [ -z "$line" ]; then
    line=$(printf '%s\n' "$OUT" | first_line)
  fi
  printf '%s' "$line"
}

# --- host facts ---------------------------------------------------------------

probed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
host=$(hostname -f 2>/dev/null || uname -n 2>/dev/null || true)
user=$(id -un 2>/dev/null || true)
os_name=""
if [ -r /etc/os-release ]; then
  os_name=$(. /etc/os-release 2>/dev/null; printf '%s' "${PRETTY_NAME:-}")
fi
if [ -z "$os_name" ]; then
  os_name=$(uname -sr 2>/dev/null || true)
fi

# --- interpreter --------------------------------------------------------------

interpreter_json=null
interpreter_path=""
for candidate in python3 python; do
  interpreter_path=$(type -P "$candidate" 2>/dev/null || true)
  if [ -n "$interpreter_path" ]; then
    run "$interpreter_path" --version
    version=$(printf '%s\n' "$OUT" | first_line)
    interpreter_json="{\"name\": $(json_string "$candidate"), \"path\": $(json_string "$interpreter_path"), \"version\": $(json_string_or_null "$version")}"
    break
  fi
done

# --- Archivematica version evidence -------------------------------------------

# The installed package's metadata: a dist-info in the virtualenv the
# interpreter belongs to (or the install role's), or an egg-info on PYTHONPATH.
am_version=""
am_source=""
am_location=""
package_dirs=()
if [ -n "$interpreter_path" ]; then
  venv=$(dirname "$(dirname "$interpreter_path")")
  package_dirs+=("$venv"/lib/python*/site-packages)
fi
package_dirs+=(/usr/share/archivematica/virtualenvs/archivematica/lib/python*/site-packages)
if [ -n "${PYTHONPATH:-}" ]; then
  IFS=: read -r -a pythonpath_dirs <<< "$PYTHONPATH"
  package_dirs+=(${pythonpath_dirs[@]+"${pythonpath_dirs[@]}"})
fi
for package_dir in ${package_dirs[@]+"${package_dirs[@]}"}; do
  [ -d "$package_dir" ] || continue
  for metadata in "$package_dir"/archivematica-*.dist-info/METADATA "$package_dir"/archivematica.egg-info/PKG-INFO; do
    [ -r "$metadata" ] || continue
    am_version=$(LC_ALL=C sed -n 's/^Version:[[:space:]]*//p' "$metadata" | first_line)
    if [ -n "$am_version" ]; then
      am_source="package-metadata"
      am_location="$metadata"
      break 2
    fi
  done
done

# The source checkout: a git repository above the package metadata (an
# editable install) or in the install role's directories, described by its
# tag or commit and its branch.
checkout_json=null
checkout_dirs=()
if [ -n "$am_location" ]; then
  parent=$(dirname "$am_location")
  while [ -n "$parent" ] && [ "$parent" != "/" ] && [ "$parent" != "." ]; do
    checkout_dirs+=("$parent")
    parent=$(dirname "$parent")
  done
fi
checkout_dirs+=(/opt/archivematica/archivematica /opt/archivematica)
for checkout in ${checkout_dirs[@]+"${checkout_dirs[@]}"}; do
  [ -e "$checkout/.git" ] || continue
  run git -c safe.directory='*' -C "$checkout" describe --tags --always --dirty
  [ "$RC" -eq 0 ] || continue
  description=$(printf '%s\n' "$OUT" | first_line)
  run git -c safe.directory='*' -C "$checkout" rev-parse --abbrev-ref HEAD
  branch=""
  if [ "$RC" -eq 0 ]; then
    branch=$(printf '%s\n' "$OUT" | first_line)
  fi
  checkout_json="{\"path\": $(json_string "$checkout"), \"description\": $(json_string "$description"), \"branch\": $(json_string_or_null "$branch")}"
  if [ -z "$am_version" ]; then
    am_version="$description"
    am_source="git"
    am_location="$checkout"
  fi
  break
done
archivematica_json="{\"version\": $(json_string_or_null "$am_version"), \"source\": $(json_string_or_null "$am_source"), \"location\": $(json_string_or_null "$am_location"), \"checkout\": $checkout_json}"

# --- executables --------------------------------------------------------------

executables_json=""
for name in ${NAMES[@]+"${NAMES[@]}"}; do
  path=$(type -P "$name" 2>/dev/null || true)
  if [ -n "$path" ]; then
    version=$(probe_version "$name" "$path")
    entry="{\"path\": $(json_string "$path"), \"version\": $(json_string_or_null "$version")}"
  else
    entry='{"path": null, "version": null}'
  fi
  executables_json="$executables_json${executables_json:+, }$(json_string "$name"): $entry"
done

# --- ffmpeg codec lists -------------------------------------------------------

codec_names() {
  LC_ALL=C awk 'listing && NF >= 2 { print $2 } /^ *-----/ { listing = 1 }'
}

# Lists the codec names of one ffmpeg listing in CODECS, or sets CODEC_ERROR
# when the listing failed, timed out or named nothing.
list_codecs() {
  CODECS=""
  CODEC_ERROR=""
  run "$ffmpeg_path" -hide_banner "$1"
  if [ "$RC" -ne 0 ]; then
    CODEC_ERROR="ffmpeg $1 exited $RC: $(printf '%s\n' "$OUT" | first_line)"
    return
  fi
  CODECS=$(printf '%s\n' "$OUT" | codec_names)
  if [ -z "$CODECS" ]; then
    CODEC_ERROR="ffmpeg $1 listed nothing"
  fi
}

ffmpeg_json=null
ffmpeg_path=$(type -P ffmpeg 2>/dev/null || true)
if [ -n "$ffmpeg_path" ]; then
  list_codecs -decoders
  decoders=$CODECS
  decoder_error=$CODEC_ERROR
  list_codecs -encoders
  encoders=$CODECS
  encoder_error=$CODEC_ERROR
  if [ -z "$decoder_error" ] && [ -z "$encoder_error" ]; then
    # shellcheck disable=SC2086
    ffmpeg_json="{\"decoders\": $(json_array $decoders), \"encoders\": $(json_array $encoders)}"
  else
    ffmpeg_json="{\"decoders\": null, \"encoders\": null, \"error\": $(json_string "${decoder_error:-$encoder_error}")}"
  fi
fi

# --- option checks ------------------------------------------------------------

checks_json=""
add_check() {
  local check
  check="{\"id\": $(json_string "$1"), \"subject\": $(json_string "$2"), \"result\": $(json_string "$3"), \"output\": $(json_string_or_null "$4")}"
  checks_json="$checks_json${checks_json:+, }$check"
}

inkscape_path=$(type -P inkscape 2>/dev/null || true)
if [ -n "$inkscape_path" ]; then
  for option in -z --export-pdf=/dev/null --export-plain-svg=/dev/null; do
    run "$inkscape_path" "$option" --version
    if [ "$RC" -ne 0 ]; then
      result=rejected
      line=$(printf '%s\n' "$OUT" | first_line)
    elif printf '%s\n' "$OUT" | LC_ALL=C grep -qi deprecated; then
      result=deprecated
      line=$(printf '%s\n' "$OUT" | first_match 'deprecated')
    else
      result=accepted
      line=$(printf '%s\n' "$OUT" | first_line)
    fi
    add_check inkscape-option "${option%%=*}" "$result" "$line"
  done
fi

convert_path=$(type -P convert 2>/dev/null || true)
if [ -n "$convert_path" ]; then
  run "$convert_path" -version
  if printf '%s\n' "$OUT" | LC_ALL=C grep -qi deprecated; then
    add_check convert-deprecation "convert -version" deprecated "$(printf '%s\n' "$OUT" | first_match 'deprecated')"
  else
    add_check convert-deprecation "convert -version" ok "$(printf '%s\n' "$OUT" | first_line)"
  fi
fi

# --- document -----------------------------------------------------------------

printf '{\n'
printf '  "probe_version": 1,\n'
printf '  "probed_at": %s,\n' "$(json_string "$probed_at")"
printf '  "host": %s,\n' "$(json_string_or_null "$host")"
printf '  "user": %s,\n' "$(json_string_or_null "$user")"
printf '  "os": %s,\n' "$(json_string_or_null "$os_name")"
printf '  "path": %s,\n' "$(json_string "${PATH:-}")"
printf '  "interpreter": %s,\n' "$interpreter_json"
printf '  "archivematica": %s,\n' "$archivematica_json"
printf '  "executables": {%s},\n' "$executables_json"
printf '  "ffmpeg": %s,\n' "$ffmpeg_json"
printf '  "checks": [%s]\n' "$checks_json"
printf '}\n'
