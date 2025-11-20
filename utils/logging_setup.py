import logging
from pathlib import Path


LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True, parents=True)


def setup_logging(level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger("isup_bridge")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    fmt = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Console handler
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    ch.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.addHandler(ch)

    # File handlers
    fh = logging.FileHandler(LOG_DIR / "isup_bridge.log", encoding="utf-8")
    fh.setFormatter(fmt)
    fh.setLevel(logging.DEBUG)
    logger.addHandler(fh)

    eh = logging.FileHandler(LOG_DIR / "errors.log", encoding="utf-8")
    eh.setFormatter(fmt)
    eh.setLevel(logging.ERROR)
    logger.addHandler(eh)

    return logger
