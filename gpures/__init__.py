try:
    from importlib.metadata import version
    __version__ = version("gpures")
except ImportError:
    __version__ = "0.1.0"
