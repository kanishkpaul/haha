#!/usr/bin/env bash
# Stop the Resume-Matcher servers started by start-servers.sh.
lsof -ti tcp:8000 -sTCP:LISTEN | xargs -r kill
lsof -ti tcp:3000 -sTCP:LISTEN | xargs -r kill
