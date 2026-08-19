"""Registers reef's tables and migration folder with Piccolo."""

import os

from piccolo.conf.apps import AppConfig

from reef.models import TABLES

APP_CONFIG = AppConfig(
    # Piccolo writes this against every applied migration and filters on it,
    # so it cannot simply be changed: a database whose records say "rif" would
    # look, to an app called "reef", like one that has never migrated. The
    # records are moved first, before Piccolo reads them, by adopt_app_name()
    # in scripts/migrate.py. Changing this string without that step replays
    # the whole chain against a populated database.
    app_name="reef",
    migrations_folder_path=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "piccolo_migrations"
    ),
    table_classes=list(TABLES),
)
