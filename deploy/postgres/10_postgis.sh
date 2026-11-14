#!/bin/sh
# Replaces the init script of the postgis/postgis image, which on a new database also installs
# topology and the TIGER geocoder (US address data, dozens of tables Alembic would read as
# foreign ones). Migration 0063 creates the only extension the platform uses: postgis.
# Like every init script, it runs only when the data directory is empty.
:
