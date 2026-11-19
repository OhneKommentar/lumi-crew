"""Lumi Crew: a multi-agent coding CLI."""
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("lumi-crew")
except PackageNotFoundError:          # running from a source checkout without installation
    __version__ = "0.0.0+dev"
