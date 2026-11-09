"""Ordered schema migrations. Add new modules and register them in ALL."""

from wowahf.db.migrations import m001_initial

ALL = [
    m001_initial,
]
