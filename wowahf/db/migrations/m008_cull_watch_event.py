VERSION = "008_cull_watch_event"

_EVENT_GRANT_HINT = (
    "wowahf needs the EVENT privilege, e.g.: "
    "GRANT EVENT ON `wow_ahf`.* TO 'wowahf'@'%'; "
    "FLUSH PRIVILEGES; "
    "Also ensure event_scheduler=ON on MariaDB."
)


def upgrade(conn) -> None:
    """
    Every 8 hours: delete inactive (active=0) rows from ah_watch_*.
    Discovers house tables via information_schema so new houses are covered.
    """
    with conn.cursor() as cur:
        cur.execute("DROP PROCEDURE IF EXISTS cull_inactive_ah_watch")
        cur.execute(
            """
            CREATE PROCEDURE cull_inactive_ah_watch()
            BEGIN
                DECLARE done INT DEFAULT 0;
                DECLARE tbl VARCHAR(64);
                DECLARE cur_tables CURSOR FOR
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = DATABASE()
                      AND table_name REGEXP '^ah_watch_[0-9]+$';
                DECLARE CONTINUE HANDLER FOR NOT FOUND SET done = 1;

                OPEN cur_tables;
                read_loop: LOOP
                    FETCH cur_tables INTO tbl;
                    IF done = 1 THEN
                        LEAVE read_loop;
                    END IF;

                    -- Batched deletes via a loop keep large houses from one huge txn.
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
            END
            """
        )

        try:
            cur.execute("DROP EVENT IF EXISTS cull_ah_watch_8h")
            cur.execute(
                """
                CREATE EVENT cull_ah_watch_8h
                    ON SCHEDULE EVERY 8 HOUR
                    STARTS (UTC_TIMESTAMP() + INTERVAL 10 MINUTE)
                    ON COMPLETION PRESERVE
                    ENABLE
                    COMMENT 'Delete ah_watch_* rows with active=0'
                    DO CALL cull_inactive_ah_watch()
                """
            )
        except Exception as exc:
            raise RuntimeError(
                f"Failed to create cull_ah_watch_8h event: {exc}. {_EVENT_GRANT_HINT}"
            ) from exc

        try:
            cur.execute("SET GLOBAL event_scheduler = ON")
        except Exception as exc:
            from wowahf.utils.log import log

            log(
                "db: warning: could not enable event_scheduler "
                f"(set event_scheduler=ON in MariaDB config): {exc}"
            )
