"""Allows running the package as `python -m music_loader`."""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
