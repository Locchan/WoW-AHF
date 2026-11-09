VERSION = "011_space_optimize"

# Keep this many newest parses of ah_raw_* (stats needs current + previous).
RAW_PARSE_KEEP = 2

# Compress hypertables once a chunk is older than this (data retained).
COMPRESS_AFTER = "3 days"


def upgrade(conn) -> None:
    """
    Space pass: drop pgloader duplicate indexes, tighten house-table types,
    keep only last N raw parses, promote market_*/aggr/trans to Timescale
    hypertables with compression (no history deletion).
    """
    with conn.cursor() as cur:
        cur.execute("SET statement_timeout = 0")
        cur.execute("SET lock_timeout = '10min'")
        _drop_pgloader_duplicate_indexes(cur)
        conn.commit()
        _ensure_time_left_enum(cur)
        conn.commit()
        _tighten_and_rekey_raw(cur)
        conn.commit()
        _tighten_watch(cur)
        conn.commit()
        _tighten_aggr_trans(cur)
        conn.commit()
        _replace_raw_purge(cur)
        conn.commit()
        _hypertable_market(cur)
        conn.commit()
        _hypertable_house_series(cur)
        conn.commit()
        _enable_compression_policies(cur)
        conn.commit()
    # VACUUM cannot run inside a transaction block.
    prev = conn.autocommit
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("VACUUM (ANALYZE)")
    finally:
        conn.autocommit = prev


def _drop_pgloader_duplicate_indexes(cur) -> None:
    cur.execute(
        """
        SELECT schemaname, indexname
        FROM pg_indexes
        WHERE schemaname IN (
            'public', 'data_raw', 'data_aggregated',
            'data_watch', 'data_transactions'
        )
          AND indexname ~ '^idx_[0-9]+_'
          AND indexname !~ '_primary$'
          AND indexname !~ '_uq_'
        """
    )
    for schema, name in cur.fetchall():
        cur.execute(f'DROP INDEX IF EXISTS "{schema}"."{name}"')


def _ensure_time_left_enum(cur) -> None:
    cur.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'auc_time_left') THEN
            CREATE TYPE auc_time_left AS ENUM (
              'SHORT', 'MEDIUM', 'LONG', 'VERY_LONG'
            );
          END IF;
        END
        $$
        """
    )


def _pk_name(cur, schema: str, table: str) -> str | None:
    cur.execute(
        """
        SELECT c.conname
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = %s AND t.relname = %s AND c.contype = 'p'
        """,
        (schema, table),
    )
    row = cur.fetchone()
    return row[0] if row else None


def _tighten_and_rekey_raw(cur) -> None:
    cur.execute(
        """
        SELECT tablename FROM pg_tables
        WHERE schemaname = 'data_raw' AND tablename ~ '^ah_raw_[0-9]+$'
        ORDER BY 1
        """
    )
    for (table,) in cur.fetchall():
        q = f"data_raw.{table}"
        cur.execute(
            f"ALTER TABLE {q} "
            f"ALTER COLUMN item_id TYPE INT, "
            f"ALTER COLUMN auc_amount TYPE INT"
        )
        cur.execute(
            f"ALTER TABLE {q} "
            f"ALTER COLUMN auc_time_left TYPE auc_time_left "
            f"USING auc_time_left::auc_time_left"
        )
        pk = _pk_name(cur, "data_raw", table)
        if pk:
            cur.execute(f'ALTER TABLE {q} DROP CONSTRAINT "{pk}"')
        cur.execute(
            f"""
            DO $$
            BEGIN
              IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'data_raw'
                  AND table_name = '{table}'
                  AND column_name = 'id'
              ) THEN
                ALTER TABLE {q} DROP COLUMN id;
              END IF;
            END
            $$
            """
        )
        cur.execute(
            f"ALTER TABLE {q} ADD PRIMARY KEY (auc_id, parse_id)"
        )
        # Covered by the new PK.
        cur.execute(f"DROP INDEX IF EXISTS data_raw.idx_{table}_auc_parse")


def _tighten_watch(cur) -> None:
    cur.execute(
        """
        SELECT tablename FROM pg_tables
        WHERE schemaname = 'data_watch' AND tablename ~ '^ah_watch_[0-9]+$'
        ORDER BY 1
        """
    )
    for (table,) in cur.fetchall():
        cur.execute(
            f"""
            ALTER TABLE data_watch.{table}
              ALTER COLUMN first_time_left TYPE auc_time_left
              USING first_time_left::auc_time_left
            """
        )


def _tighten_aggr_trans(cur) -> None:
    cur.execute(
        """
        SELECT schemaname, tablename
        FROM pg_tables
        WHERE (schemaname = 'data_aggregated' AND tablename ~ '^ah_aggr_[0-9]+$')
           OR (schemaname = 'data_transactions' AND tablename ~ '^ah_trans_[0-9]+$')
        ORDER BY 1, 2
        """
    )
    for schema, table in cur.fetchall():
        q = f"{schema}.{table}"
        cur.execute(
            f"ALTER TABLE {q} "
            f"ALTER COLUMN item_id TYPE INT, "
            f"ALTER COLUMN amount TYPE INT"
        )
        cur.execute(
            f"""
            DO $$
            BEGIN
              IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = '{schema}'
                  AND table_name = '{table}'
                  AND column_name = 'parsed_at'
              ) THEN
                ALTER TABLE {q} ADD COLUMN parsed_at TIMESTAMPTZ;
              END IF;
            END
            $$
            """
        )
        cur.execute(
            f"""
            UPDATE {q} t
            SET parsed_at = p.parsed_at
            FROM parses p
            WHERE t.parsed_at IS NULL AND p.id = t.parse_id
            """
        )
        # Orphans (should not exist); use epoch so NOT NULL can apply.
        cur.execute(
            f"""
            UPDATE {q}
            SET parsed_at = TIMESTAMPTZ '1970-01-01'
            WHERE parsed_at IS NULL
            """
        )
        cur.execute(f"ALTER TABLE {q} ALTER COLUMN parsed_at SET NOT NULL")


def _replace_raw_purge(cur) -> None:
    cur.execute(
        f"""
        CREATE OR REPLACE FUNCTION maintenance_purge_old_ah_raw()
        RETURNS void
        LANGUAGE plpgsql
        AS $$
        DECLARE
            r RECORD;
            run_id BIGINT;
            err TEXT;
            keep_ids BIGINT[];
        BEGIN
            INSERT INTO schema_event_runs (event_name, started_at)
            VALUES ('purge_ah_raw_daily', clock_timestamp())
            RETURNING id INTO run_id;
            BEGIN
                SELECT coalesce(array_agg(id), ARRAY[]::BIGINT[])
                INTO keep_ids
                FROM (
                    SELECT id FROM parses ORDER BY id DESC LIMIT {RAW_PARSE_KEEP}
                ) k;

                FOR r IN
                    SELECT tablename
                    FROM pg_tables
                    WHERE schemaname = 'data_raw'
                      AND tablename ~ '^ah_raw_[0-9]+$'
                LOOP
                    IF cardinality(keep_ids) = 0 THEN
                        EXECUTE format('TRUNCATE data_raw.%I', r.tablename);
                    ELSE
                        EXECUTE format(
                            'DELETE FROM data_raw.%I WHERE parse_id <> ALL($1)',
                            r.tablename
                        ) USING keep_ids;
                    END IF;
                END LOOP;

                UPDATE schema_event_runs
                SET finished_at = clock_timestamp(), ok = TRUE
                WHERE id = run_id;
            EXCEPTION WHEN OTHERS THEN
                GET STACKED DIAGNOSTICS err = MESSAGE_TEXT;
                UPDATE schema_event_runs
                SET finished_at = clock_timestamp(),
                    ok = FALSE,
                    error_message = left(err, 512)
                WHERE id = run_id;
                RAISE;
            END;
        END;
        $$
        """
    )


def _drop_unique_and_pk(cur, schema: str, table: str) -> None:
    cur.execute(
        """
        SELECT c.conname, c.contype
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = %s AND t.relname = %s AND c.contype IN ('p', 'u')
        """,
        (schema, table),
    )
    for name, _ctype in cur.fetchall():
        cur.execute(
            f'ALTER TABLE "{schema}"."{table}" DROP CONSTRAINT "{name}"'
        )


def _add_parsed_at_market(cur, table: str) -> None:
    cur.execute(
        f"""
        DO $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = '{table}'
              AND column_name = 'parsed_at'
          ) THEN
            ALTER TABLE public.{table} ADD COLUMN parsed_at TIMESTAMPTZ;
          END IF;
        END
        $$
        """
    )
    cur.execute(
        f"""
        UPDATE public.{table} m
        SET parsed_at = p.parsed_at
        FROM parses p
        WHERE m.parsed_at IS NULL AND p.id = m.parse_id
        """
    )
    cur.execute(
        f"""
        UPDATE public.{table}
        SET parsed_at = TIMESTAMPTZ '1970-01-01'
        WHERE parsed_at IS NULL
        """
    )
    cur.execute(f"ALTER TABLE public.{table} ALTER COLUMN parsed_at SET NOT NULL")
    # pgloader widened some ints
    if table != "market_stats":
        cur.execute(
            f"""
            ALTER TABLE public.{table}
              ALTER COLUMN ah_id TYPE INT,
              ALTER COLUMN item_id TYPE INT
            """
        )
    else:
        cur.execute(
            f"ALTER TABLE public.{table} ALTER COLUMN ah_id TYPE INT"
        )


def _hypertable_market(cur) -> None:
    for table, uq_cols, drop_id in (
        ("market_stats", "(ah_id, metric, parse_id, parsed_at)", True),
        ("market_price", "(ah_id, item_id, parse_id, parsed_at)", True),
        ("market_depth", "(ah_id, item_id, parse_id, parsed_at)", True),
        ("market_spread", "(ah_id, item_id, parse_id, parsed_at)", True),
    ):
        _add_parsed_at_market(cur, table)
        _drop_unique_and_pk(cur, "public", table)
        cur.execute(
            f"""
            DO $$
            BEGIN
              IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = '{table}'
                  AND column_name = 'id'
              ) THEN
                ALTER TABLE public.{table} DROP COLUMN id;
              END IF;
            END
            $$
            """
        )
        cur.execute(
            f"""
            SELECT create_hypertable(
                'public.{table}',
                by_range('parsed_at'),
                migrate_data => TRUE,
                if_not_exists => TRUE
            )
            """
        )
        cur.execute(
            f"""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_{table}_ht
            ON public.{table} {uq_cols}
            """
        )


def _hypertable_house_series(cur) -> None:
    cur.execute(
        """
        SELECT schemaname, tablename
        FROM pg_tables
        WHERE (schemaname = 'data_aggregated' AND tablename ~ '^ah_aggr_[0-9]+$')
           OR (schemaname = 'data_transactions' AND tablename ~ '^ah_trans_[0-9]+$')
        ORDER BY 1, 2
        """
    )
    for schema, table in cur.fetchall():
        q = f"{schema}.{table}"
        _drop_unique_and_pk(cur, schema, table)
        cur.execute(
            f"""
            DO $$
            BEGIN
              IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = '{schema}'
                  AND table_name = '{table}'
                  AND column_name = 'id'
              ) THEN
                ALTER TABLE {q} DROP COLUMN id;
              END IF;
            END
            $$
            """
        )
        cur.execute(
            f"""
            SELECT create_hypertable(
                '{q}',
                by_range('parsed_at'),
                migrate_data => TRUE,
                if_not_exists => TRUE
            )
            """
        )
        cur.execute(
            f"""
            CREATE INDEX IF NOT EXISTS idx_{table}_parse_item
            ON {q} (parse_id, item_id)
            """
        )


def _enable_compression_policies(cur) -> None:
    specs = [
        ("public.market_stats", "ah_id, metric"),
        ("public.market_price", "ah_id, item_id"),
        ("public.market_depth", "ah_id, item_id"),
        ("public.market_spread", "ah_id, item_id"),
    ]
    cur.execute(
        """
        SELECT format('%I.%I', schemaname, tablename)
        FROM pg_tables
        WHERE (schemaname = 'data_aggregated' AND tablename ~ '^ah_aggr_[0-9]+$')
           OR (schemaname = 'data_transactions' AND tablename ~ '^ah_trans_[0-9]+$')
        """
    )
    for (rel,) in cur.fetchall():
        specs.append((rel, "item_id"))

    for rel, segmentby in specs:
        cur.execute(
            f"""
            ALTER TABLE {rel} SET (
                timescaledb.compress,
                timescaledb.compress_segmentby = '{segmentby}',
                timescaledb.compress_orderby = 'parsed_at DESC, parse_id DESC'
            )
            """
        )
        cur.execute(
            f"""
            SELECT add_compression_policy(
                '{rel}',
                compress_after => INTERVAL '{COMPRESS_AFTER}',
                if_not_exists => TRUE
            )
            """
        )
