VERSION = "013_raw_keep3"

# Need current + previous for the next parse, plus one spare so purge
# cannot wipe prev before transactions are computed.
RAW_PARSE_KEEP = 3


def upgrade(conn) -> None:
    with conn.cursor() as cur:
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
