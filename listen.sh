#!/usr/bin/env sh
cd "$(dirname "$0")/tools" && exec node review_server.mjs "$@"
