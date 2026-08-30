# Imports
from importlib.metadata import PackageNotFoundError, version
from amp.bot import Bot


try:
    __version__ = version("amp-mc")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"

__all__ = ["Bot", "__version__"]
