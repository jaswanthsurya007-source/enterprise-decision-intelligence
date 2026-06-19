"""EDIS integration (L2) Alembic migrations package.

The Alembic ``env.py`` and the ``versions/`` scripts are loaded by Alembic via
file path, not imported as modules. This ``__init__`` exists only so the wheel
build (hatchling ``packages=["migrations"]``) recognizes the directory.
"""
