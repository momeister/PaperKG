"""Domain APIRouters split out of the monolithic api/product_main.py.

Each module here exposes a ``router`` (FastAPI ``APIRouter``) that product_main
includes via ``app.include_router(...)``. Handlers reference shared module globals
through attribute access (e.g. ``dataset_clients.search_datasets``) so existing test
monkeypatches keep working.
"""
