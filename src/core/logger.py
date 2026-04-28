import logging
import sys

def setup_logging():
    """Configure base logger format for the entire application."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)-25s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout
    )

def get_logger(name: str) -> logging.Logger:
    """Returns a standardized namespaced logger."""
    return logging.getLogger(f"ScopeReview.{name}")
