-- New default: fill missing coverage. Preserve prior choices for a reviewable upgrade.
INSERT OR IGNORE INTO settings(key,value_json,updated_at_utc)
VALUES('pre_missing_only_policy',json_object(
  'collection_scope_v1',json((SELECT value_json FROM settings WHERE key='collection_scope_v1')),
  'automatic_public_deep',json((SELECT value_json FROM settings WHERE key='automatic_public_deep'))
),strftime('%Y-%m-%dT%H:%M:%SZ','now'));

UPDATE settings SET value_json=json_set(value_json,'$.policy.reconcile_enabled',json('false')),
 updated_at_utc=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE key='collection_scope_v1';
UPDATE settings SET value_json=json_set(value_json,'$.refresh_expired',json('false')),
 updated_at_utc=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE key='automatic_public_deep';
INSERT OR REPLACE INTO settings(key,value_json,updated_at_utc)
VALUES('collection_default_policy','{"mode":"MISSING_ONLY","refresh_requires_explicit_choice":true}',strftime('%Y-%m-%dT%H:%M:%SZ','now'));
