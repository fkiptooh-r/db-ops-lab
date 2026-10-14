#!/usr/bin/env bash
# Show the role of each replica set member (PRIMARY / SECONDARY / ...).
# Asks each node in turn, so it still works while one or two nodes are down.
#
# Usage:
#   scripts/rs_roles.sh                one-off check
#   scripts/rs_roles.sh --watch        refresh every 2 s (Ctrl+C to stop)
#   scripts/rs_roles.sh --watch 5      refresh every 5 s
#
# Exit code: 0 if a node answered, 1 if none did.

NODES="mongo1:27017 mongo2:27018 mongo3:27019"

rs_roles() {
  local c
  for c in $NODES; do
    docker exec "${c%:*}" mongosh --quiet --port "${c#*:}" --eval \
      'rs.status().members.forEach(m => print(m.name.padEnd(14), m.stateStr.padEnd(10), "health=" + m.health))' \
      2>/dev/null && return 0
  done
  echo "no node reachable"
  return 1
}

if [ "${1:-}" = "--watch" ]; then
  interval="${2:-2}"
  while true; do
    clear
    date +%T
    rs_roles
    sleep "$interval"
  done
else
  rs_roles
fi
