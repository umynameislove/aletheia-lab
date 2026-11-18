"""Additive K-10 projection DDL; applied by the existing persistence migrator."""

from aletheia_lab.hosted_auth.postgres_sessions import Connection


async def stamp_catalog_revision(conn: Connection, workspace: str) -> None:
    """Grant mutations enlist invalidation without altering frozen K-04 DDL."""
    row = await (await conn.execute("SELECT to_regclass('k10_catalog_revisions') IS NOT NULL")).fetchone()
    if row == (True,):
        await conn.execute('INSERT INTO k10_catalog_revisions(workspace_id,revision) VALUES(%s,1) '
                           'ON CONFLICT(workspace_id) DO UPDATE SET revision=k10_catalog_revisions.revision+1',
                           (workspace,))

READ_PROJECTION_TABLES = ("k10_catalog_revisions", "k10_result_views", "k10_result_items", "k10_evidence_links")

READ_PROJECTION_DDL = """
CREATE TABLE k10_catalog_revisions (
 workspace_id text PRIMARY KEY REFERENCES j01_workspaces(workspace_id),
 revision bigint NOT NULL DEFAULT 0 CHECK(revision>=0));
INSERT INTO k10_catalog_revisions(workspace_id) SELECT workspace_id FROM j01_workspaces;
CREATE FUNCTION k10_catalog_changed() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 INSERT INTO k10_catalog_revisions(workspace_id,revision)
 VALUES(CASE WHEN TG_OP='DELETE' THEN OLD.workspace_id ELSE NEW.workspace_id END,1)
 ON CONFLICT(workspace_id) DO UPDATE SET revision=k10_catalog_revisions.revision+1;
 RETURN NULL;
END $$;
CREATE FUNCTION k10_records_inserted() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 INSERT INTO k10_catalog_revisions(workspace_id,revision)
 SELECT DISTINCT workspace_id,1 FROM new_records
 ON CONFLICT(workspace_id) DO UPDATE SET revision=k10_catalog_revisions.revision+1;
 RETURN NULL;
END $$;
CREATE TRIGGER k10_records_inserted AFTER INSERT ON k05_records
 REFERENCING NEW TABLE AS new_records FOR EACH STATEMENT EXECUTE FUNCTION k10_records_inserted();
CREATE TRIGGER k10_records_changed AFTER UPDATE OR DELETE ON k05_records
 FOR EACH ROW EXECUTE FUNCTION k10_catalog_changed();
CREATE TRIGGER k10_projects_changed AFTER INSERT OR UPDATE OR DELETE ON k05_projects
 FOR EACH ROW EXECUTE FUNCTION k10_catalog_changed();
CREATE TRIGGER k10_pointers_changed AFTER INSERT OR UPDATE OR DELETE ON k05_current_pointers
 FOR EACH ROW EXECUTE FUNCTION k10_catalog_changed();
CREATE TABLE k10_result_views (
 workspace_id text NOT NULL, result_id text NOT NULL, projection_version integer NOT NULL CHECK(projection_version>0),
 project_id text NOT NULL, snapshot_id text NOT NULL,
 canonical_sha256 text NOT NULL CHECK(canonical_sha256~'^[0-9a-f]{64}$'),
 projection_sha256 text NOT NULL CHECK(projection_sha256~'^[0-9a-f]{64}$'),
 header text NOT NULL CHECK(octet_length(header)<=32768),
 header_sha256 text NOT NULL CHECK(header_sha256~'^[0-9a-f]{64}$'),
 claim_count integer NOT NULL CHECK(claim_count>=0), evidence_count integer NOT NULL CHECK(evidence_count>=0),
 node_count integer NOT NULL CHECK(node_count>=0), edge_count integer NOT NULL CHECK(edge_count>=0),
 PRIMARY KEY(workspace_id,result_id,projection_version),
 FOREIGN KEY(workspace_id,result_id) REFERENCES k05_records(workspace_id,record_id));
CREATE TABLE k10_result_items (
 workspace_id text NOT NULL, result_id text NOT NULL, projection_version integer NOT NULL,
 kind text NOT NULL CHECK(kind IN ('claim','evidence','node','edge')), item_id text NOT NULL,
 payload text NOT NULL CHECK(octet_length(payload)<=65536),
 sha256 text NOT NULL CHECK(sha256~'^[0-9a-f]{64}$'),
 source_id text, target_id text, search_text text NOT NULL DEFAULT '',
 search_document tsvector GENERATED ALWAYS AS (to_tsvector('simple'::regconfig,search_text)) STORED,
 PRIMARY KEY(workspace_id,result_id,projection_version,kind,item_id),
 FOREIGN KEY(workspace_id,result_id,projection_version)
 REFERENCES k10_result_views(workspace_id,result_id,projection_version) DEFERRABLE INITIALLY DEFERRED,
 CHECK((kind='edge')=(source_id IS NOT NULL AND target_id IS NOT NULL)));
CREATE INDEX k10_result_items_search ON k10_result_items USING gin(search_document);
CREATE TABLE k10_evidence_links (
 workspace_id text NOT NULL, result_id text NOT NULL, projection_version integer NOT NULL,
 claim_id text NOT NULL, evidence_id text NOT NULL, relation text NOT NULL CHECK(relation IN ('citation','counterevidence')),
 PRIMARY KEY(workspace_id,result_id,projection_version,claim_id,evidence_id),
 FOREIGN KEY(workspace_id,result_id,projection_version)
 REFERENCES k10_result_views(workspace_id,result_id,projection_version) DEFERRABLE INITIALLY DEFERRED);
"""
