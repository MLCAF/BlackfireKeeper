"""Writable application paths for source and portable folder distributions."""
import sys
from pathlib import Path


def application_root():
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]
