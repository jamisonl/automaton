import logging
import os
import sys
import io


def create_unicode_safe_stream():
    try:
        if hasattr(sys.stdout, "buffer"):
            return io.TextIOWrapper(
                sys.stdout.buffer,
                encoding="utf-8",
                errors="replace",
                newline="\n",
                line_buffering=True,
            )
        else:
            return sys.stdout
    except (AttributeError, OSError):
        return sys.stdout


def setup_logger():
    """
    Configures and returns a logger instance.
    The log level is determined by the DEBUG environment variable.
    """
    log_level_str = os.getenv("DEBUG", "false").lower()
    log_level = logging.DEBUG if log_level_str == "true" else logging.INFO

    logger = logging.getLogger("llm_agent")
    logger.setLevel(log_level)

    unicode_safe_stream = create_unicode_safe_stream()
    ch = logging.StreamHandler(unicode_safe_stream)
    ch.setLevel(log_level)

    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    ch.setFormatter(formatter)

    if not logger.handlers:
        logger.addHandler(ch)

    return logger


logger = setup_logger()
