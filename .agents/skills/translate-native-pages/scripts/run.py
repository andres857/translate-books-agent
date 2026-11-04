#!/usr/bin/env python3
"""Entrada de la skill translate-native-pages; ejecutar con el entorno del proyecto."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))
from astro_translate.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["translate-native-pages", *sys.argv[1:]]))
