"""Cluster Research Assistant."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("cluster-research-assist")
except PackageNotFoundError:  # running from a checkout without an install
    __version__ = "0.0.0"

__all__ = ["__version__"]
