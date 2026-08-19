"""Registers reef's tables and migration folder with Piccolo."""

import os

from piccolo.conf.apps import AppConfig

from reef.models import TABLES

APP_CONFIG = AppConfig(
    # Stays "rif" forever. This is not a name anybody reads -- it is the
    # key Piccolo writes into the `migration` table for every migration it
    # applies, and `get_migrations_which_ran()` filters on it. Rename it and
    # Piccolo finds zero applied migrations under the new key and replays
    # the entire chain against a populated production database.
    #
    # The module around it is called reef; this string is what the database
    # remembers, and the database was never renamed. Same reasoning as the
    # rif_* helper functions, the rif/rif_authz/rif_probe roles, and the
    # RIF_* variables on Railway. See the README's note on the name.
    app_name="rif",
    migrations_folder_path=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "piccolo_migrations"
    ),
    table_classes=list(TABLES),
)
