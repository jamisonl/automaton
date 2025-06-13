import logging
import os
import sys
import io


class StableStreamHandler(logging.StreamHandler):
    def emit(self, record):
        if self.stream is None or getattr(self.stream, "closed", False):

            try:
                current_stderr = sys.stderr
                if current_stderr and not getattr(current_stderr, "closed", False):
                    print(
                        f"LOGGER_ERROR: Stream '{self.stream}' closed or None. Record: {self.format(record)}",
                        file=current_stderr,
                    )
                    current_stderr.flush()
            except Exception:
                pass
            return
        try:
            super().emit(record)
        except Exception as e:
            try:
                current_stderr = sys.stderr
                if current_stderr and not getattr(current_stderr, "closed", False):
                    print(
                        f"LOGGER_EMIT_ERROR: Error during emit: {e}. Record: {self.format(record)}",
                        file=current_stderr,
                    )
                    current_stderr.flush()
            except Exception:
                pass


def setup_logger(output_stream=None, log_level_override=None):
    """
    Configures and returns a logger instance.
    The log level is determined by the DEBUG environment variable or override.
    The stream for the handler is determined by output_stream or defaults to sys.stderr.
    """
    if log_level_override is not None:
        log_level = log_level_override
    else:
        log_level_str = os.getenv("DEBUG", "false").lower()
        log_level = logging.DEBUG if log_level_str == "true" else logging.INFO

    logger_instance = logging.getLogger("llm_agent")
    logger_instance.setLevel(log_level)

    if logger_instance.hasHandlers():
        logger_instance.handlers.clear()

    handler_stream = None
    stream_description = "default sys.stderr"

    if output_stream and not getattr(output_stream, "closed", False):
        handler_stream = output_stream
        stream_description = f"provided stream ({output_stream})"
    else:
        current_stderr = sys.stderr
        if current_stderr and not getattr(current_stderr, "closed", False):
            if hasattr(current_stderr, "buffer") and not isinstance(
                current_stderr, io.TextIOWrapper
            ):
                try:
                    handler_stream = io.TextIOWrapper(
                        current_stderr.buffer,
                        encoding="utf-8",
                        errors="replace",
                        newline="\n",
                        line_buffering=True,
                    )
                    stream_description = "wrapped current sys.stderr"
                except Exception:
                    handler_stream = current_stderr
                    stream_description = "raw current sys.stderr (wrapping failed)"
            else:
                handler_stream = current_stderr
                stream_description = "current sys.stderr as-is"
        else:
            handler_stream = io.StringIO()
            stream_description = "StringIO (no valid system stream)"
            print(
                "LOGGER_SETUP_CRITICAL: No valid output stream available. Logging to internal StringIO.",
                file=(
                    sys.stderr
                    if sys.stderr and not getattr(sys.stderr, "closed", False)
                    else open(os.devnull, "w")
                ),
            )

    ch = StableStreamHandler(handler_stream)

    ch.setLevel(log_level)
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    ch.setFormatter(formatter)
    logger_instance.addHandler(ch)

    diag_stream = (
        sys.stderr
        if sys.stderr and not getattr(sys.stderr, "closed", False)
        else open(os.devnull, "w")
    )

    return logger_instance


logger = logging.getLogger("llm_agent")
if not logger.hasHandlers():
    logger.addHandler(logging.NullHandler())
