"""Hermes cron pre-run wrapper: copy (not symlink) to ~/.hermes/scripts/cards_preflight.py.

Hermes 0.21.3 only runs job scripts that resolve inside ~/.hermes/scripts/, so a
symlink into dotfiles is refused. This file stays put and runs the maintained
preflight from the dotfiles checkout.
"""
from pathlib import Path
import runpy

runpy.run_path(str(Path.home() / "dotfiles/cards/bin/cards_preflight.py"), run_name="__main__")
