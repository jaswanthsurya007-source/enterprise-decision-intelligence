"""EDIS one-off operational scripts (Z1).

Made a package so the pure, infra-free helpers in :mod:`scripts.seed_demo`
(scenario construction + story / copilot-answer formatting) are importable by the
unit tests without running the live docker-compose driver.
"""
