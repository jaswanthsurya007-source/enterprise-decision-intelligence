"""Governance background consumers (audit, lineage).

Each consumer subscribes to a governance topic via
:func:`edis_platform.bus.base.make_source`, deserializes with
:func:`edis_platform.bus.base.parse_message`, and folds the event into the
governance store. They are launched as asyncio background tasks at app startup
and connect lazily (never at import time).
"""
