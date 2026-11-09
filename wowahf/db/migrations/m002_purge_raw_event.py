VERSION = "002_purge_raw_event"

_EVENT_GRANT_HINT = (
    "wowahf needs the EVENT privilege, e.g.: "
    "GRANT EVENT ON `wow_ahf`.* TO 'wowahf'@'%'; "
    "FLUSH PRIVILEGES; "
    "Also ensure event_scheduler=ON on MariaDB."
)


def upgrade(conn) -> None:
    """
    Daily event: delete ah_raw_* rows whose parse is older than 24 hours.
    Discovers house tables via information_schema so new houses are covered.
    """
    with conn.cursor() as cur:
        cur.execute("DROP PROCEDURE IF EXISTS purge_old_ah_raw")
        cur.execute(
            """
            CREATE PROCEDURE purge_old_ah_raw()
            BEGIN
                DECLARE done INT DEFAULT 0;
                DECLARE tbl VARCHAR(64);
                DECLARE cur_tables CURSOR FOR
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = DATABASE()
                      AND table_name REGEXP '^ah_raw_[0-9]+$';
                DECLARE CONTINUE HANDLER FOR NOT FOUND SET done = 1;

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
            END
            """
        )

        try:
            cur.execute("DROP EVENT IF EXISTS purge_ah_raw_daily")
            cur.execute(
                """
                CREATE EVENT purge_ah_raw_daily
                    ON SCHEDULE EVERY 1 DAY
                    STARTS (UTC_TIMESTAMP() + INTERVAL 1 HOUR)
                    ON COMPLETION PRESERVE
                    ENABLE
                    COMMENT 'Purge ah_raw_* rows older than 24h by parse time'
                    DO CALL purge_old_ah_raw()
                """
            )
        except Exception as exc:
            raise RuntimeError(
                f"Failed to create purge_ah_raw_daily event: {exc}. {_EVENT_GRANT_HINT}"
            ) from exc

        try:
            cur.execute("SET GLOBAL event_scheduler = ON")
        except Exception as exc:
            from wowahf.utils.log import log

            log(
                "db: warning: could not enable event_scheduler "
                f"(set event_scheduler=ON in MariaDB config): {exc}"
            )
