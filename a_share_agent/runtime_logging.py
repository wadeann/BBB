from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_runtime_logging(project_root: str | Path, process: str = "agent") -> logging.Logger:
    root = Path(project_root).resolve()
    log_dir = root / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    safe = "".join(ch if (ch.isalnum() or ch in "_-.") else "_" for ch in (process or "agent"))
    logger = logging.getLogger("a_share_agent")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    # Reconfigure once per CLI process. This avoids duplicate handlers in tests.
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass

    fmt = logging.Formatter("%(asctime)s %(levelname)s %(process)d %(threadName)s %(name)s %(message)s")
    main_handler = RotatingFileHandler(
        log_dir / f"{safe}.log", maxBytes=10 * 1024 * 1024, backupCount=7, encoding="utf-8"
    )
    main_handler.setLevel(logging.INFO)
    main_handler.setFormatter(fmt)
    logger.addHandler(main_handler)

    error_handler = RotatingFileHandler(
        log_dir / "error.log", maxBytes=10 * 1024 * 1024, backupCount=14, encoding="utf-8"
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(fmt)
    logger.addHandler(error_handler)
    return logger
