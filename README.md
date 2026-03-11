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

The bundled `libreoffice_pdf` package set installs `libreoffice` together with
the distro-specific provider of `/usr/bin/flock`. Its default normalization
command uses a host-local lock file at `/var/lib/archivematica/documentConversion.lock`
so the same command works across Ubuntu and EL variants.

## Role layout

The Ansible role lives at the repository root with the usual subdirectories
(`defaults/`, `handlers/`, `meta/`, `tasks/`, `templates/`, `tests/`). The
role validates the supported platform, installs any required MCPClient-side
packages, renders a temporary Python script, and runs it through the
Archivematica dashboard virtualenv after exporting the environment defined in
`/etc/default/archivematica-dashboard` on Ubuntu or
`/etc/sysconfig/archivematica-dashboard` on EL.

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
dashboard runner so the role's validation, templating, cleanup, and
idempotence behavior can be checked without a full Archivematica deployment.
