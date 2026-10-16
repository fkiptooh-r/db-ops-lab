#!/usr/bin/env bash
# Show replication lag for each secondary (how far behind the primary it is).
# Asks each node in turn, so it still works while one or two nodes are down.
#
# Usage:
#   scripts/rs_lag.sh                one-off check
#   scripts/rs_lag.sh --watch        refresh every 2 s (Ctrl+C to stop)
#   scripts/rs_lag.sh --watch 5      refresh every 5 s
#
# Lag is the gap between the primary's last write and the secondary's last
# applied write. 0-2 s is normal under load. A down member shows "unknown".
#
# Exit code: 0 if a node answered, 1 if none did.

NODES="mongo1:27017 mongo2:27018 mongo3:27019"

rs_lag() {
  local c
  for c in $NODES; do
    docker exec "${c%:*}" mongosh --quiet --port "${c#*:}" --eval '
      const s = rs.status();
      const p = s.members.find(m => m.stateStr === "PRIMARY");
      s.members.forEach(m => {
        let lag = "";
        if (m.stateStr !== "PRIMARY") {
          if (m.health !== 1) lag = "lag=unknown (down)";
          else if (!p) lag = "lag=unknown (no PRIMARY)";
          else lag = "lag=" + Math.max(0, (p.optimeDate - m.optimeDate) / 1000) + "s";
        }
        print(m.name.padEnd(14), m.stateStr.padEnd(26), lag);
      })' 2>/dev/null && return 0
  done
  echo "no node reachable"
  return 1
}

if [ "${1:-}" = "--watch" ]; then
  interval="${2:-2}"
  while true; do
    clear
    date +%T
    rs_lag
    sleep "$interval"
  done
else
  rs_lag
fi
