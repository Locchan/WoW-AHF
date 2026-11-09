VERSION = "009_event_run_log"


def upgrade(conn) -> None:
    """
    Log each scheduled-event procedure run (ok / error) so the app can
    detect failures. INFORMATION_SCHEMA.EVENTS has no failure column.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_event_runs (
                id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                event_name VARCHAR(64) NOT NULL,
                started_at DATETIME(6) NOT NULL,
                finished_at DATETIME(6) NULL,
                ok TINYINT(1) NULL,
                error_message VARCHAR(512) NULL,
                INDEX idx_ser_event_started (event_name, started_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )

        # --- purge_old_ah_raw (used by purge_ah_raw_daily) ---
        cur.execute("DROP PROCEDURE IF EXISTS purge_old_ah_raw")
        cur.execute(
            """
            CREATE PROCEDURE purge_old_ah_raw()
            BEGIN
                DECLARE done INT DEFAULT 0;
                DECLARE tbl VARCHAR(64);
                DECLARE run_id BIGINT;
                DECLARE err_msg VARCHAR(512);

                DECLARE cur_tables CURSOR FOR
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = DATABASE()
                      AND table_name REGEXP '^ah_raw_[0-9]+$';
                DECLARE CONTINUE HANDLER FOR NOT FOUND SET done = 1;
                DECLARE EXIT HANDLER FOR SQLEXCEPTION
                BEGIN
                    GET DIAGNOSTICS CONDITION 1 err_msg = MESSAGE_TEXT;
                    UPDATE schema_event_runs
                    SET finished_at = UTC_TIMESTAMP(6),
                        ok = 0,
                        error_message = LEFT(IFNULL(err_msg, 'unknown'), 512)
                    WHERE id = run_id;
                    RESIGNAL;
                END;

                INSERT INTO schema_event_runs (event_name, started_at)
                VALUES ('purge_ah_raw_daily', UTC_TIMESTAMP(6));
                SET run_id = LAST_INSERT_ID();

                OPEN cur_tables;
                read_loop: LOOP
                    FETCH cur_tables INTO tbl;
                    IF done = 1 THEN
                        LEAVE read_loop;
                    END IF;

                    SET @purge_sql = CONCAT(
                        'DELETE r FROM `', tbl, '` r ',
                        'INNER JOIN parses p ON p.id = r.parse_id ',
                        'WHERE p.parsed_at < UTC_TIMESTAMP(6) - INTERVAL 24 HOUR'
                    );
                    PREPARE stmt FROM @purge_sql;
                    EXECUTE stmt;
                    DEALLOCATE PREPARE stmt;
                END LOOP;
                CLOSE cur_tables;

                UPDATE schema_event_runs
                SET finished_at = UTC_TIMESTAMP(6), ok = 1
                WHERE id = run_id;
            END
            """
        )

        # --- cull_inactive_ah_watch (used by cull_ah_watch_8h) ---
        cur.execute("DROP PROCEDURE IF EXISTS cull_inactive_ah_watch")
        cur.execute(
            """
            CREATE PROCEDURE cull_inactive_ah_watch()
            BEGIN
                DECLARE done INT DEFAULT 0;
                DECLARE tbl VARCHAR(64);
                DECLARE run_id BIGINT;
                DECLARE err_msg VARCHAR(512);

                DECLARE cur_tables CURSOR FOR
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = DATABASE()
                      AND table_name REGEXP '^ah_watch_[0-9]+$';
                DECLARE CONTINUE HANDLER FOR NOT FOUND SET done = 1;
                DECLARE EXIT HANDLER FOR SQLEXCEPTION
                BEGIN
                    GET DIAGNOSTICS CONDITION 1 err_msg = MESSAGE_TEXT;
                    UPDATE schema_event_runs
                    SET finished_at = UTC_TIMESTAMP(6),
                        ok = 0,
                        error_message = LEFT(IFNULL(err_msg, 'unknown'), 512)
                    WHERE id = run_id;
                    RESIGNAL;
                END;

                INSERT INTO schema_event_runs (event_name, started_at)
                VALUES ('cull_ah_watch_8h', UTC_TIMESTAMP(6));
                SET run_id = LAST_INSERT_ID();

                OPEN cur_tables;
                read_loop: LOOP
                    FETCH cur_tables INTO tbl;
                    IF done = 1 THEN
                        LEAVE read_loop;
                    END IF;

                    house_batch: LOOP
                        SET @cull_sql = CONCAT(
                            'DELETE FROM `', tbl, '` WHERE active = 0 LIMIT 5000'
                        );
                        PREPARE stmt FROM @cull_sql;
                        EXECUTE stmt;
                        SET @cull_rows = ROW_COUNT();
                        DEALLOCATE PREPARE stmt;
                        IF @cull_rows < 5000 THEN
                            LEAVE house_batch;
                        END IF;
                    END LOOP;
                END LOOP;
                CLOSE cur_tables;

                UPDATE schema_event_runs
                SET finished_at = UTC_TIMESTAMP(6), ok = 1
                WHERE id = run_id;
            END
            """
        )
