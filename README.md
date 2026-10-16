# Archivematica FPR Ansible Role

This project provides a reusable Ansible role for site-specific
Archivematica Format Policy Registry customization. It is meant for production
installations where operators want to manage FPR tools, commands, and rules
through Ansible instead of one-off scripts or manual dashboard changes.

The role is intentionally named for the broader problem space:
`archivematica_fpr`.
That keeps future work in one place when the customization still belongs to
the same domain, for example:

- adding a new normalization command
- enabling or disabling existing FPR commands or rules
- installing package dependencies required by an FPR-driven tool
- adding rule sets for a site-specific group of formats

This role is intended to cover site-specific FPR customizations that still
belong to the same operational domain. A LibreOffice PDF normalization
workflow, an OCR rule set, and a custom verification command are all FPR
customizations and should share the same dashboard execution model, package
mapping, and variable interface.

Use a separate role only when the automation extends beyond FPR
configuration. Examples:

- provisioning and operating an external conversion service
- managing a standalone document-processing stack unrelated to Archivematica's
  dashboard FPR objects
- shipping a large workflow with its own service lifecycle and monitoring

## Current functionality

The role currently supports a LibreOffice-based access-normalization workflow
for office document formats. It handles:

- a standalone role repository layout that can be added to deployment
  environment `requirements.yml` files
- MCPClient-side package installation via named package sets
- dashboard-side FPR creation and update through the Django ORM
- idempotent creation or replacement of the LibreOffice tool, event-detail
  command, normalization command, and access rules for configured source PUIDs
- an opt-in, read-only inventory of the registry and of the MCPClient host's
  executables, rendered as JSON and Markdown on the controller (see
  [Registry inventory](#registry-inventory))

The bundled `libreoffice_pdf` package set installs `libreoffice` together with
the distro-specific provider of `/usr/bin/flock`. Its default normalization
command uses a host-local lock file at `/var/lib/archivematica/documentConversion.lock`
so the same command works across Ubuntu and EL variants.

## Role layout

The Ansible role lives at the repository root with the usual subdirectories
(`defaults/`, `files/`, `handlers/`, `meta/`, `tasks/`, `templates/`,
`tests/`). The role validates the supported platform, installs any required
MCPClient-side packages, renders a temporary Python script, and runs it through
the Archivematica dashboard virtualenv after exporting the environment defined
in `/etc/default/archivematica-dashboard` on Ubuntu or
`/etc/sysconfig/archivematica-dashboard` on EL. `files/` holds the registry
inventory's dump statement, probe script and reporter (see
[Registry inventory](#registry-inventory)).

## Quick start

```yaml
- hosts: archivematica
  become: true
  roles:
    - role: archivematica_fpr
      vars:
        archivematica_fpr_enabled_package_sets:
          - libreoffice_pdf
        archivematica_fpr_libreoffice_pdf_source_format_pronom_ids:
          - fmt/40
          - fmt/412
          - fmt/61
          - fmt/214
          - fmt/126
          - fmt/215
```

## Variables

- `archivematica_fpr_manage_mcpclient_packages`: Whether the role installs
  package prerequisites on the target host.
- `archivematica_fpr_enabled_package_sets`: Named dependency sets to install
  for the enabled FPR customization.
- `archivematica_fpr_mcpclient_package_set_map`: Per-distro package mapping
  for MCPClient-side dependency sets. The bundled `libreoffice_pdf` set
  installs `libreoffice` together with the distro-specific provider of
  `/usr/bin/flock`.
- `archivematica_fpr_extra_mcpclient_packages`: Additional site-specific
  packages that do not belong to a reusable package set.
- `archivematica_fpr_dashboard_environment_file_map`: Default dashboard
  environment file path by OS family.
- `archivematica_fpr_dashboard_environment_file`: Resolved dashboard
  environment file path used when running ORM updates.
- `archivematica_fpr_dashboard_python_path`: Path to the dashboard virtualenv
  Python interpreter.
- `archivematica_fpr_libreoffice_pdf_lock_file`: Host-local lock file used by
  command implementations that need serialized LibreOffice access.
- `archivematica_fpr_libreoffice_pdf_tool_description` /
  `archivematica_fpr_libreoffice_pdf_tool_version`: Metadata for the FPR tool.
- `archivematica_fpr_libreoffice_pdf_event_detail_description` /
  `archivematica_fpr_libreoffice_pdf_event_detail_command`: Event-detail
  command definition for LibreOffice.
- `archivematica_fpr_libreoffice_pdf_command_description` /
  `archivematica_fpr_libreoffice_pdf_command`: The normalization command
  definition.
- `archivematica_fpr_libreoffice_pdf_output_format_pronom_id`: Output format
  PUID for the normalization command.
- `archivematica_fpr_libreoffice_pdf_verification_command_uuid` /
  `archivematica_fpr_libreoffice_pdf_verification_command_description`: How the
  role resolves the verification command referenced by the normalization
  command.
- `archivematica_fpr_libreoffice_pdf_rule_purpose`: Rule purpose, defaulting
  to `access`.
- `archivematica_fpr_libreoffice_pdf_source_format_pronom_ids`: Source PUIDs
  that should normalize to PDF through LibreOffice.

See `defaults/main.yml` for the full variable set.

## Registry inventory

An opt-in, read-only inventory of the Format Policy Registry and of the
executables the MCPClient host offers to its commands. With
`archivematica_fpr_inventory_enabled: true` the role, after its other tasks:

1. dumps the nine `fpr_*` tables of the MCP database as one JSON document with
   the `mysql` client, using the credentials of the dashboard environment file
   (`fpr-raw.json`, statement in `files/dump_fpr.sql`);
2. probes the executables the commands start, resolved through the `PATH` of
   the MCPClient environment file as the `archivematica` user, with their
   versions, ffmpeg's decoders and encoders and the Inkscape and ImageMagick
   option checks (`probe.json`, script in `files/fpr-probe.sh`);
3. records the run (`meta.json`) with the other two files under
   `/var/lib/archivematica/fpr-inventory/<UTC timestamp>/` on the host, fetches
   them to `<playbook_dir>/fpr-inventory/<inventory_hostname>/<timestamp>/` on
   the controller and, when `uv` is on the controller's PATH, renders them with
   `files/fpr_inventory.py` into `fpr-inventory.json` and `fpr-inventory.md`.

The run changes no registry row, service, package or file outside its own
output directory, and every inventory task reports no change. It does not use
the dashboard virtualenv or any Archivematica code: the role's other tasks
change the registry through ORM scripts run in the dashboard virtualenv, while
the inventory only reads, with SQL through the client, so it also works while
the dashboard is down or an upgrade is half done, on every release from 1.13
and on hosts installed by either generation of the `archivematica-src` role.
The Molecule scenario covers Ubuntu 22.04, Ubuntu 24.04 and Rocky 9, the
platforms in `meta/main.yml`; the older platforms that 1.13 to 1.17 hosts run
are untested.

The report opens with the provenance: the host, OS and service PATH, the
interpreter, the Archivematica package version and source checkout, and the
registry's applied migrations with the latest one, so that two hosts that both
say "1.19.0" can still be told apart. It then lists every command with its
text, usage, tool, script type,
executables, shell utilities, Python imports, placeholders, output, linked
commands, status (`enabled`, `enabled-superseded`, `superseded`, `disabled`)
and rules grouped by purpose with their formats and the same statuses; the
executables with the
probe's verdict and version; the tools; the identification commands and tools;
the placeholders and client-script conventions; and the observations of the
named checks: `enabled-superseded` (a command or rule row enabled although
a newer row replaces it), `usage-case`, `disabled-tool`,
`disabled-format-version`, `no-enabled-rules`, `unreferenced-linked-command`,
`sudo-or-absolute-path`, `executable-missing` (an executable an enabled
command or identification command calls is absent from the host; the
executables table lists every probed one), `forced-decoder-missing`,
`encoder-missing`, `option-deprecated` and `option-rejected`.

### Requirements

- The `mysql` client on the host. The `percona` role installs the Percona
  client package beside the server; a host installed otherwise needs the
  distribution's or Percona's client package, or
  `archivematica_fpr_inventory_mysql_client` pointed at the client. The dump
  statement needs the JSON aggregation functions of MySQL 5.7.22+, 8.x
  (Percona) or MariaDB 10.5+, and raises MariaDB's `group_concat_max_len` for
  the session. The client runs with `--defaults-file`, so no other option file
  (such as root's `~/.my.cnf`) can redirect it; only a `.mylogin.cnf` login
  path still applies, and a socket or protocol that the global option files
  used to supply goes in `archivematica_fpr_inventory_mysql_client_args`.
- The dashboard environment file, with `ARCHIVEMATICA_DASHBOARD_CLIENT_HOST`,
  `_DATABASE`, `_USER` and `_PASSWORD` (`_PORT` is optional and defaults to
  3306), and the MCPClient environment file, with the service `PATH`. The
  `archivematica-src` role writes both in every generation; the role reads them
  as root, so their permissions do not matter, and parses them as systemd
  environment files with `files/fpr-env.py` on the host's own interpreter
  (Python 3.6 or later) rather than executing them, so a secret key with
  shell characters is harmless. When the MCPClient file sets no `PATH`, the
  probe uses systemd's default service `PATH`.
- The probe user (`archivematica`) and `runuser` (util-linux) on the host: the
  probe is started by root and drops privileges with `runuser`, so no ACL
  support for Ansible's unprivileged `become` is needed.
- Gathered facts, for the run timestamp and the OS-family environment file maps.
- `uv` on the controller for the rendering step. Without it the files are
  still fetched and the play prints the command to render them later.

### Running it

From a deployment repository, with the playbook that already applies the role:

```bash
ansible-playbook -i envs/<env>/hosts envs/<env>/archivematica-fpr.yml \
  -e target=<host or group> -e archivematica_fpr_inventory_enabled=true
```

With no package sets or LibreOffice PUIDs configured, the role's other tasks do
nothing, so the run is read-only end to end. A host that fails leaves the other
hosts' inventories intact. Useful extra variables:

- `archivematica_fpr_inventory_render_all=true` includes disabled and
  superseded rows in the report.
- `archivematica_fpr_inventory_render=false` fetches only.
- `archivematica_fpr_inventory_extra_executables='["libreoffice"]'` probes a
  site's additions.
- `archivematica_fpr_inventory_mysql_defaults_extra_file=/root/.my.cnf` reads
  with a protected client defaults file (passed as `--defaults-file`, so it is
  the only option file read) instead of the dashboard credentials.
- `archivematica_fpr_inventory_mysql_client_args='["--protocol=tcp"]'` passes
  non-secret client arguments, for a non-default socket, host or TLS setting.
- `archivematica_fpr_inventory_no_log=false` shows the dump task's output, to
  debug on a non-production host.

Add `fpr-inventory/` to the deployment repository's `.gitignore` before the
first run: the fetched files hold every command's full text, which may include
site-specific scripts.

### Rendering again, or without Ansible

The reporter is one file with inline script metadata; it needs Python 3.12 or
later and no third-party package to build the inventory:

```bash
uv run --script files/fpr_inventory.py build \
  --raw fpr-inventory/<host>/<ts>/fpr-raw.json \
  --probe fpr-inventory/<host>/<ts>/probe.json \
  --meta fpr-inventory/<host>/<ts>/meta.json \
  --out fpr-inventory/<host>/<ts>/ --all
```

`build` needs only the raw dump; `--probe` adds the host's executables and
`--meta` the run's facts. Without Ansible, against a reachable database, the
same raw dump comes from `collect` (the only subcommand with a dependency):

```bash
uv run --with pymysql --script files/fpr_inventory.py collect \
  --database-url mysql://user:password@host:3306/MCP --out fpr-raw.json
```

and a probe from running `files/fpr-probe.sh` where the MCPClient runs, for
example inside the development stack's container, where the environment is
already set:

```bash
docker compose exec -T archivematica-mcp-client bash -s -- --env-file /dev/null \
  7z convert exiftool ffmpeg ffprobe fido fiwalk gs inkscape jhove magick \
  mediaconch mediainfo ps2pdf python3 readpst sf tesseract tsk_recover \
  unrar unrar-nonfree < files/fpr-probe.sh > probe.json
```

`compare` is reserved for a later comparison against pristine dumps.

### The collected files

- `fpr-raw.json`: `dumped_at` (server UTC time), the nine tables
  (`fpcommand`, `fptool`, `fprule`, `formatversion`, `format`, `formatgroup`,
  `idcommand`, `idtool`, `idrule`) as arrays of rows keyed by column, the
  columns listed in `files/dump_fpr.sql`, with the referenced row's UUID in
  place of every foreign key and the values as the server returns them (UUIDs
  as strings, `enabled` as 0 or 1, datetimes as text), and `migrations`, the
  `fpr` rows of `django_migrations` (`id`, `app`, `name`, `applied`), which
  say which migrations shaped the registry and when.
- `probe.json`: `probed_at`, `host`, `user`, `os`, `path` (the PATH used),
  `interpreter` (`name`, `path`, `version`), `archivematica` (`version`,
  `source` and `location` of the package metadata found on disk, and
  `checkout`: the `path`, git `description` and `branch` of the source
  checkout found above that metadata or under `/opt/archivematica`, null when
  there is none), `executables`
  (name to `path` and `version`, both null when missing; `ps2pdf`, a wrapper,
  reports the `gs` version), `ffmpeg` (`decoders` and `encoders` names; null
  without ffmpeg, and both lists null with an `error` when a listing failed,
  which the reporter treats as unknown rather than absent) and `checks`
  (`id`, `subject`,
  `result`, `output`): `inkscape-option` for `-z`, `--export-pdf` and
  `--export-plain-svg` (`accepted`, `deprecated` or `rejected`) and
  `convert-deprecation` (`ok` or `deprecated`).
- `meta.json`: `run_id` (the host's UTC clock when the run started), host
  facts, `role_version` (the git description of the role directory when it is
  a checkout of its own, otherwise `unknown`), the environment files and
  client used, the probed names, the files' sizes and the dump's row counts.

Both documents carry `contract_version` 1 (the probe as `probe_version`);
anything that produces them feeds the reporter.

### Inventory variables

- `archivematica_fpr_inventory_enabled`: Run the inventory tasks. Default:
  `false`.
- `archivematica_fpr_inventory_base_dir`: Host-side base; each run writes
  `<base>/<UTC timestamp>/`, a 0750 root directory holding 0600 files.
  Default: `/var/lib/archivematica/fpr-inventory`.
- `archivematica_fpr_inventory_fetch`: Fetch the run's files to the
  controller. Default: `true`.
- `archivematica_fpr_inventory_fetch_dir`: Controller-side base; files land in
  `<dir>/<inventory_hostname>/<timestamp>/`. Default:
  `{{ playbook_dir }}/fpr-inventory`.
- `archivematica_fpr_inventory_render`: Run the reporter on the controller
  after fetching, when `uv` is on its PATH. Default: `true`.
- `archivematica_fpr_inventory_render_all`: Pass `--all` to the reporter, so
  that disabled and superseded rows appear. Default: `false`.
- `archivematica_fpr_inventory_probe_user`: User the probe runs as. Default:
  `archivematica`.
- `archivematica_fpr_inventory_probe_timeout`: Seconds each version or option
  call may take. Default: `20`.
- `archivematica_fpr_inventory_executables`: Executables the probe resolves.
  Default: the stock list in `defaults/main.yml`.
- `archivematica_fpr_inventory_extra_executables`: Site additions, for example
  `libreoffice`. Default: `[]`.
- `archivematica_fpr_inventory_mysql_client`: Client executable name or path.
  Default: `mysql`.
- `archivematica_fpr_inventory_mysql_defaults_extra_file`: Protected client
  defaults file used, as `--defaults-file`, instead of the one generated from
  the dashboard environment file (mirrors
  `archivematica_upgrade_mysql_defaults_extra_file`). Default: empty.
- `archivematica_fpr_inventory_mysql_client_args`: Non-secret extra client
  arguments (mirrors `archivematica_upgrade_mysql_client_args`). Default: `[]`.
- `archivematica_fpr_inventory_no_log`: Keep the dump task's output out of the
  log. Default: `true`.
- `archivematica_fpr_inventory_tmp_dir`: Where the temporary directory holding
  the client files is created. Default:
  `{{ archivematica_fpr_dashboard_tmp_dir }}`.
- `archivematica_fpr_mcpclient_environment_file_map`: MCPClient environment
  file by OS family. Default: `/etc/default/archivematica-mcp-client` on
  Debian, `/etc/sysconfig/archivematica-mcp-client` on RedHat.
- `archivematica_fpr_mcpclient_environment_file`: The MCPClient environment
  file, overridable like the dashboard one. Default: resolved from the map.

The existing `archivematica_fpr_dashboard_environment_file` (and its map) gives
the dump its credentials.

## Naming recommendation

Repository name:

- `ansible-archivematica-fpr`

Galaxy role name:

- `archivematica_fpr`

That gives deployment repos a stable generic dependency to reference, while
keeping individual customizations as vars and playbook data instead of turning
each one into its own role repository.

## Testing

The included Ansible test playbook is a minimal syntax target for local
validation:

```bash
ansible-playbook --syntax-check tests/test.yml
```

For containerized role testing with Molecule and Docker:

```bash
uv sync --group molecule
uv run molecule test
```

The Molecule scenario exercises supported Debian and EL containers with a fake
dashboard runner, a fake `mysql` client that prints the fixture dump, fake
environment files and a fake executable on the service PATH, so the role's
validation, templating, cleanup, idempotence and inventory behavior can be
checked without a full Archivematica deployment.

The reporter and the environment file reader are tested on fixture files
(the development stack's registry dump and the probe of its MCPClient image,
under `tests/fixtures/`, and sample environment files), type-checked and
linted:

```bash
uv sync --group test
uv run pytest
uv run mypy
uv run ruff check files tests
```
