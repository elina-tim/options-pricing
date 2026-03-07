"""
conftest.py — Adds the project root to sys.path so that
`from api import ...` works in all tests regardless of
how pytest is invoked (CLI or PyCharm).
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent))
