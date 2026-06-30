"""Code-Werkstatt: registered coding-project folders + safe file/git access.

See :mod:`workspace.manager` for the filesystem/git logic. Persistence of the
registrations lives in the central DuckDB (``code_projects`` table in
``storage.metadata_db``); the HTTP surface is in ``api.product_main``.
"""
