"""Registers rif's tables and migration folder with Piccolo."""

import os

from piccolo.conf.apps import AppConfig

from rif.models import TABLES

APP_CONFIG = AppConfig(
    app_name="rif",
    migrations_folder_path=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "piccolo_migrations"
    ),
    table_classes=list(TABLES),
)
