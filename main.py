from wowahf.db.connection import connect
from wowahf.db.migrate import migrate
from wowahf.jobs import build_job_manager
from wowahf.utils.log import log
from wowahf.utils.shutdown import install_signal_handlers


if __name__ == "__main__":
    install_signal_handlers()
    log("scheduler: ready (SIGTERM/SIGINT will shut down cleanly)")

    log("scheduler: running migrations")
    conn = connect()
    try:
        migrate(conn)
    finally:
        conn.close()

    manager = build_job_manager()
    manager.start()
    manager.wait_forever()
    log("scheduler: shutdown complete")
