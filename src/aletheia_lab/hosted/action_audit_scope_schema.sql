ALTER TABLE k06_action_audit ADD COLUMN parent_id text;
ALTER TABLE k06_action_audit ADD COLUMN subject_id text;
ALTER TABLE k06_action_audit ADD CONSTRAINT k06_audit_parent_id_v2
    CHECK (parent_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$');
ALTER TABLE k06_action_audit ADD CONSTRAINT k06_audit_subject_id_v2
    CHECK (subject_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$');
