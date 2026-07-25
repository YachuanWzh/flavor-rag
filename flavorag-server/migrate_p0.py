"""Apply P0 schema changes to SQLite DB — adds missing columns and tables."""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "flavorag_dev.db")
conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

# Add pipeline_id to t_knowledge_base
try:
    cur.execute("ALTER TABLE t_knowledge_base ADD COLUMN pipeline_id VARCHAR(20)")
    print("[OK] Added pipeline_id to t_knowledge_base")
except sqlite3.OperationalError as e:
    if "duplicate column" in str(e).lower():
        print("[SKIP] pipeline_id already exists")
    else:
        raise

# Create new P0 tables
tables = [
    # t_biz_change_log
    """CREATE TABLE IF NOT EXISTS t_biz_change_log (
        id               TEXT PRIMARY KEY,
        biz_type         TEXT NOT NULL,
        biz_id           TEXT NOT NULL,
        operation_type   TEXT NOT NULL,
        action_desc      TEXT,
        before_snapshot  TEXT,
        after_snapshot   TEXT,
        change_diff      TEXT,
        operator_id      TEXT,
        operator_name    TEXT,
        operator_role    TEXT,
        success          INTEGER NOT NULL DEFAULT 1,
        error_message    TEXT,
        class_name       TEXT,
        method_name      TEXT,
        ip               TEXT,
        user_agent       TEXT,
        create_time      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",
    # t_knowledge_document_schedule
    """CREATE TABLE IF NOT EXISTS t_knowledge_document_schedule (
        id                TEXT PRIMARY KEY,
        doc_id            TEXT NOT NULL UNIQUE,
        kb_id             TEXT NOT NULL,
        cron_expr         TEXT,
        enabled           INTEGER DEFAULT 0,
        next_run_time     TIMESTAMP,
        last_run_time     TIMESTAMP,
        last_success_time TIMESTAMP,
        last_status       TEXT,
        last_error        TEXT,
        last_etag         TEXT,
        last_modified     TEXT,
        last_content_hash TEXT,
        lock_owner        TEXT,
        lock_until        TIMESTAMP,
        create_time       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        update_time       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",
    # t_knowledge_document_schedule_exec
    """CREATE TABLE IF NOT EXISTS t_knowledge_document_schedule_exec (
        id            TEXT PRIMARY KEY,
        schedule_id   TEXT NOT NULL,
        doc_id        TEXT NOT NULL,
        kb_id         TEXT NOT NULL,
        status        TEXT NOT NULL,
        message       TEXT,
        start_time    TIMESTAMP,
        end_time      TIMESTAMP,
        file_name     TEXT,
        file_size     INTEGER,
        content_hash  TEXT,
        etag          TEXT,
        last_modified TEXT,
        create_time   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        update_time   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",
    # t_knowledge_document_chunk_log
    """CREATE TABLE IF NOT EXISTS t_knowledge_document_chunk_log (
        id               TEXT PRIMARY KEY,
        doc_id           TEXT NOT NULL,
        status           TEXT NOT NULL,
        process_mode     TEXT,
        chunk_strategy   TEXT,
        pipeline_id      TEXT,
        extract_duration INTEGER,
        chunk_duration   INTEGER,
        embed_duration   INTEGER,
        persist_duration INTEGER,
        total_duration   INTEGER,
        chunk_count      INTEGER,
        error_message    TEXT,
        start_time       TIMESTAMP,
        end_time         TIMESTAMP,
        create_time      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        update_time      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""",
    # t_ingestion_pipeline
    """CREATE TABLE IF NOT EXISTS t_ingestion_pipeline (
        id          TEXT PRIMARY KEY,
        name        TEXT NOT NULL,
        description TEXT,
        created_by  TEXT NOT NULL,
        updated_by  TEXT,
        create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        deleted     INTEGER DEFAULT 0
    )""",
    # t_ingestion_pipeline_node
    """CREATE TABLE IF NOT EXISTS t_ingestion_pipeline_node (
        id             TEXT PRIMARY KEY,
        pipeline_id    TEXT NOT NULL,
        node_id        TEXT NOT NULL,
        node_type      TEXT NOT NULL,
        next_node_id   TEXT,
        settings_json  TEXT,
        condition_json TEXT,
        created_by     TEXT NOT NULL,
        updated_by     TEXT,
        create_time    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        update_time    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        deleted        INTEGER DEFAULT 0
    )""",
    # t_ingestion_task
    """CREATE TABLE IF NOT EXISTS t_ingestion_task (
        id               TEXT PRIMARY KEY,
        pipeline_id      TEXT NOT NULL,
        source_type      TEXT NOT NULL,
        source_location  TEXT NOT NULL,
        source_file_name TEXT,
        status           TEXT DEFAULT 'pending',
        chunk_count      INTEGER DEFAULT 0,
        error_message    TEXT,
        logs_json        TEXT,
        metadata_json    TEXT,
        started_at       TIMESTAMP,
        completed_at     TIMESTAMP,
        created_by       TEXT NOT NULL,
        updated_by       TEXT,
        create_time      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        update_time      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        deleted          INTEGER DEFAULT 0
    )""",
    # t_ingestion_task_node
    """CREATE TABLE IF NOT EXISTS t_ingestion_task_node (
        id            TEXT PRIMARY KEY,
        task_id       TEXT NOT NULL,
        pipeline_id   TEXT NOT NULL,
        node_id       TEXT NOT NULL,
        node_type     TEXT NOT NULL,
        node_order    INTEGER DEFAULT 0,
        status        TEXT DEFAULT 'pending',
        duration_ms   INTEGER,
        message       TEXT,
        error_message TEXT,
        output_json   TEXT,
        create_time   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        update_time   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        deleted       INTEGER DEFAULT 0
    )""",
]

for ddl in tables:
    table_name = ddl.split("(", 1)[0].replace("CREATE TABLE IF NOT EXISTS ", "").strip()
    try:
        cur.execute(ddl)
        print(f"[OK] Created table {table_name}")
    except sqlite3.OperationalError as e:
        print(f"[SKIP] {table_name}: {e}")

conn.commit()
conn.close()
print("\nMigration complete. Restart flavorag-server.")
