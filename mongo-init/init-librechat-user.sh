#!/usr/bin/env bash
# Runs automatically, exactly once, via the official mongo image's own
# /docker-entrypoint-initdb.d/ convention -- fires only when mongodb_data
# is being initialized for the first time (an existing volume skips it
# entirely, same as MONGO_INITDB_ROOT_USERNAME/PASSWORD themselves).
#
# Creates the LibreChat app user in *admin*, not LibreChat -- MONGO_URI
# uses authSource=admin, and Mongo looks up a user's credentials in
# whichever database authSource names, regardless of which database the
# granted role applies to. See docs/OPERATIONS.md for what creating this
# user in the wrong database looks like when it fails.
set -euo pipefail

mongosh --quiet <<JS
db.getSiblingDB("admin").createUser({
  user: "${MONGO_APP_USERNAME}",
  pwd: "${MONGO_APP_PASSWORD}",
  roles: [{ role: "readWrite", db: "LibreChat" }]
});
JS
