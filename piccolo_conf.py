"""Piccolo's entrypoint: which engine to use and which apps to migrate.

Read by the ``piccolo`` CLI. The engine is the same one the application
uses, so migrations and runtime can never point at different databases.
"""

from piccolo.conf.apps import AppRegistry

from reef.db import DB  # noqa: F401  -- the CLI looks up `DB` by name

APP_REGISTRY = AppRegistry(apps=["reef.piccolo_app"])
