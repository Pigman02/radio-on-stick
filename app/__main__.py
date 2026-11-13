from __future__ import annotations

import logging
import signal
import sys
import threading
from types import FrameType

from .app import build_app
from .config import Config
from .httpd import make_server


def main() -> int:
    config = Config.from_env()
    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger = logging.getLogger("radio")
    if not config.admin_token:
        logger.warning("RADIO_ADMIN_TOKEN is empty; admin API is disabled")
    if config.ip_hash_salt == "dev-salt-change-me":
        logger.warning("RADIO_IP_SALT is using the development default")

    app = build_app(config)
    app.player.start()
    server = make_server(app)
    host, port = server.server_address[:2]
    logger.info("radio listening on http://%s:%s", host, port)

    stop_event = threading.Event()

    def request_shutdown(signum: int, frame: FrameType | None) -> None:
        logger.info("shutting down (signal %s)", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, request_shutdown)
    signal.signal(signal.SIGTERM, request_shutdown)

    server_thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.5},
        name="radio-http",
        daemon=True,
    )
    server_thread.start()

    try:
        while not stop_event.wait(0.5):
            if not server_thread.is_alive():
                break
    finally:
        app.player.stop()
        server.shutdown()
        server_thread.join(timeout=2.0)
        server.server_close()
        app.db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
