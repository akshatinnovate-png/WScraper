from . import engines, news, reference, social  # noqa: F401  (registers sources)
from .base import REGISTRY, Source

__all__ = ["REGISTRY", "Source"]
