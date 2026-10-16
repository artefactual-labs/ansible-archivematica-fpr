-- The Format Policy Registry as one JSON document: the nine fpr_* tables as
-- arrays of rows, with the referenced row's UUID in place of every foreign key,
-- and the fpr rows of django_migrations, which say what shaped the registry.
-- Run through the mysql client in batch, raw, headerless mode:
--   mysql --batch --raw --skip-column-names MCP < dump_fpr.sql > fpr-raw.json
-- Needs the JSON aggregation functions of MySQL 5.7.22+, 8.x or MariaDB 10.5+.
-- The column lists are explicit so that a column added later changes nothing.
-- MariaDB truncates JSON_ARRAYAGG at group_concat_max_len (1 MiB by default),
-- which a registry's format versions already approach; the session limit is
-- raised first. MySQL accepts the setting and does not need it.
SET SESSION group_concat_max_len = 1073741824;
SELECT JSON_OBJECT(
  'dumped_at', UTC_TIMESTAMP(),
  'fpcommand', (SELECT IFNULL(JSON_ARRAYAGG(JSON_OBJECT('id', id, 'uuid', uuid, 'replaces', replaces_id, 'enabled', enabled, 'lastmodified', lastmodified, 'description', description, 'command', command, 'script_type', script_type, 'output_location', output_location, 'command_usage', command_usage, 'tool', tool_id, 'output_format', output_format_id, 'verification_command', verification_command_id, 'event_detail_command', event_detail_command_id)), JSON_ARRAY()) FROM fpr_fpcommand),
  'fptool', (SELECT IFNULL(JSON_ARRAYAGG(JSON_OBJECT('id', id, 'uuid', uuid, 'description', description, 'version', version, 'enabled', enabled, 'slug', slug)), JSON_ARRAY()) FROM fpr_fptool),
  'fprule', (SELECT IFNULL(JSON_ARRAYAGG(JSON_OBJECT('id', id, 'uuid', uuid, 'replaces', replaces_id, 'enabled', enabled, 'lastmodified', lastmodified, 'purpose', purpose, 'count_attempts', count_attempts, 'count_okay', count_okay, 'count_not_okay', count_not_okay, 'command', command_id, 'format', format_id)), JSON_ARRAY()) FROM fpr_fprule),
  'formatversion', (SELECT IFNULL(JSON_ARRAYAGG(JSON_OBJECT('id', id, 'uuid', uuid, 'replaces', replaces_id, 'enabled', enabled, 'lastmodified', lastmodified, 'version', version, 'pronom_id', pronom_id, 'description', description, 'access_format', access_format, 'preservation_format', preservation_format, 'slug', slug, 'format', format_id)), JSON_ARRAY()) FROM fpr_formatversion),
  'format', (SELECT IFNULL(JSON_ARRAYAGG(JSON_OBJECT('id', id, 'uuid', uuid, 'description', description, 'slug', slug, 'group', group_id)), JSON_ARRAY()) FROM fpr_format),
  'formatgroup', (SELECT IFNULL(JSON_ARRAYAGG(JSON_OBJECT('id', id, 'uuid', uuid, 'description', description, 'slug', slug)), JSON_ARRAY()) FROM fpr_formatgroup),
  'idcommand', (SELECT IFNULL(JSON_ARRAYAGG(JSON_OBJECT('id', id, 'uuid', uuid, 'replaces', replaces_id, 'enabled', enabled, 'lastmodified', lastmodified, 'description', description, 'config', config, 'script', script, 'script_type', script_type, 'tool', tool_id)), JSON_ARRAY()) FROM fpr_idcommand),
  'idtool', (SELECT IFNULL(JSON_ARRAYAGG(JSON_OBJECT('id', id, 'uuid', uuid, 'description', description, 'version', version, 'enabled', enabled, 'slug', slug)), JSON_ARRAY()) FROM fpr_idtool),
  'idrule', (SELECT IFNULL(JSON_ARRAYAGG(JSON_OBJECT('id', id, 'uuid', uuid, 'replaces', replaces_id, 'enabled', enabled, 'lastmodified', lastmodified, 'command_output', command_output, 'command', command_id, 'format', format_id)), JSON_ARRAY()) FROM fpr_idrule),
  'migrations', (SELECT IFNULL(JSON_ARRAYAGG(JSON_OBJECT('id', id, 'app', app, 'name', name, 'applied', applied)), JSON_ARRAY()) FROM django_migrations WHERE app = 'fpr')
) AS doc;
