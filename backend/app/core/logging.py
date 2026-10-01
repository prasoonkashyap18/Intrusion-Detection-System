"""Basic application logging setup for the MVP.

Keeps logging simple: a single configured root format, no secret values,
and no dumping of uploaded network data (there is none yet at this step).
"""

from __future__ import annotations

import logging


def configure_logging(debug: bool = False) -> None:
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
