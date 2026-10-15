# Runbook: MongoDB primary down

| | |
|---|---|
| **Severity** | P2: writes pause and recover by themselves. Raise to **P1** if no new primary within 30 s. |
| **Owner** | L2 DB Support |
| **Applies to** | Replica set `rs0` (Stack A) |
| **Last tested** | 2026-10-15 |
| **Time to resolve** | Failover is automatic: under 100 ms (clean shutdown) or about 11 s (crash). Full recovery, including bringing the failed node back, about 2–3 min. |

**Nodes**

| Container | Address | Connect |
|---|---|---|
| mongo1 | mongo1:27017 | `docker exec -it mongo1 mongosh --port 27017` |
| mongo2 | mongo2:27018 | `docker exec -it mongo2 mongosh --port 27018` |
| mongo3 | mongo3:27019 | `docker exec -it mongo3 mongosh --port 27019` |

---

## Symptoms

- App or driver errors such as `AutoReconnect`, `NotWritablePrimary` or `ServerSelectionTimeoutError`.
- Writes stall for up to about 10–12 s, then resume on their own.
- `scripts/rs_roles.sh` shows one member as `(not reachable/healthy)`.
- (Once week 7 monitoring is in place) alerts: **primary changed**, **member down**.

## Impact

- **Writes:** blocked from the moment the primary is lost until a new one is elected. That's milliseconds after a clean shutdown and about 10 s after a crash.
- **Reads:** keep working for apps that read from secondaries (`readPreference=secondaryPreferred`). Apps that read only from the primary stall like writes.
- **Data:** writes acknowledged with `w: "majority"` are **not** lost, because at least one surviving node already had them.
- **In-flight writes** have an **unknown outcome**. The app may get an error even though the write was saved. In the lab, 1 of 4 "failed" writes was actually stored.
- **Redundancy:** until the failed node is back, the set runs on 2 of 3 nodes. **One more failure means no primary.**

---

## Diagnosis

Work from a **surviving** node, never the one that failed. If you don't know which node failed, run `scripts/rs_roles.sh` from `~/db-ops-lab` first.

### 1. Which member is down, and is there a new primary?

```bash
docker exec -it mongo3 mongosh --port 27019      # any surviving node
```
```js
rs.status().members.map(m => m.name + " " + m.stateStr + " health=" + m.health)
```
- **Expected (this incident):** one member `(not reachable/healthy) health=0`, one `PRIMARY`, one `SECONDARY`.
- **No PRIMARY anywhere:** a majority is lost. **Stop here** and go to [mongo-no-primary.md](mongo-no-primary.md).

### 2. Confirm the new primary

```js
db.hello().primary
```
- **Expected:** a surviving node, e.g. `mongo3:27019`.
- **Empty or `undefined`:** no primary yet. Wait 15 s and repeat. If it's still empty after 30 s, treat it as no primary (P1).

### 3. Why is the member unreachable?

```js
rs.status().members.find(m => m.health === 0).lastHeartbeatMessage
```
Paste this into the ticket.
- `Couldn't get a connection within the time limit` or `Connection refused`: the process or container is down.
- Anything about DNS or the host: a network problem rather than a process problem. Check the network before restarting anything.

Leave mongosh with `exit`.

### 4. How did the container stop?

```bash
docker ps -a --format '{{.Names}}  {{.Status}}' | grep mongo
docker inspect -f 'exit={{.State.ExitCode}} oom={{.State.OOMKilled}} at={{.State.FinishedAt}}' <failed-node>
```

| Result | Meaning |
|---|---|
| `exit=0` | Clean shutdown: someone or something stopped it on purpose |
| `exit=137 oom=false` | Force-killed: `docker kill`, a crash, or `docker stop` ran out of time |
| `exit=137 oom=true` | **Out of memory.** Check memory limits and load **before** restarting, or it will die again |
| Other non-zero | mongod error. Read the node's logs: `docker logs --tail 50 <failed-node>` |

### 5. When did the election happen, and why?

```bash
docker logs <new-primary> 2>&1 | grep -E '"Starting an election|"Election succeeded|"Member is now in state' | tail -5
```
- `Starting an election due to step up request`: **clean handover.** The old primary stepped down first, and failover took milliseconds.
- `Starting an election, since we've seen no PRIMARY in election timeout period`: **crash.** The secondaries waited out the 10 s election timeout.

Note the timestamps for the timeline (log times are UTC, so add 3 h for EAT).

---

## Resolution

### 1. Confirm the set has healed itself

If steps 1–2 show a PRIMARY, **writes are already working again**. The remaining work is restoring redundancy. Don't restart healthy nodes.

### 2. Fix the cause if needed, then restart the failed node

- **OOM (exit 137, `oom=true`):** fix memory or load first, or escalate.
- **Otherwise:**
```bash
date +%T; docker start <failed-node>
```

### 3. Watch it rejoin

```bash
cd ~/db-ops-lab
scripts/rs_roles.sh --watch 1
```
- **Expected:** `STARTUP2` or `RECOVERING`, then **`SECONDARY`**, usually within a few seconds to a minute depending on how much it must catch up.
- **Stuck in `RECOVERING` for over 5 min:** escalate (see below).

### 4. Leave the new primary where it is

All members have **priority 1**, so the returning node has no reason to call an election, and it stays SECONDARY. **Don't force it back** with `rs.stepDown()`. That causes a second write pause and gains nothing. Check priorities with:
```js
rs.conf().members.map(m => m.host + " priority=" + m.priority)
```
If a specific node must be primary (e.g. better hardware), set a higher priority in the config as a planned change, not during an incident.

---

## Verification

Run each check and compare it with the expected result.

### 1. All members healthy, exactly one primary

```bash
scripts/rs_roles.sh
```
- **Expected:** three lines, all `health=1`, exactly one `PRIMARY`.

### 2. The returning node has caught up

```bash
docker exec mongo1 mongosh --quiet --port 27017 --eval 'rs.printSecondaryReplicationInfo()'
```
- **Expected:** every secondary `0 secs (0 hrs) behind the primary`. A few seconds right after rejoining is fine; check again.

### 3. Every node has the same data

```bash
for c in mongo1:27017 mongo2:27018 mongo3:27019; do
  printf '%s  ' "$c"
  docker exec "${c%:*}" mongosh --quiet --port "${c#*:}" \
    --eval 'db.getMongo().setReadPref("secondaryPreferred"); db.getSiblingDB("shop").orders.countDocuments()'
done
```
- **Expected:** all three numbers are equal. If writes are still running, the numbers can differ slightly; stop the writes or run it twice.

### 4. The app writes normally

```bash
python scripts/load_gen.py write
```
- **Expected:** steady `ok/s`, `err/s` = 0, and the `primary` column shows the new primary.

---

## Escalation

Escalate to **L3 DBA** (or the vendor, for a confirmed MongoDB bug) when:

| Condition | Why it matters |
|---|---|
| No PRIMARY after 30 s | Majority lost. Follow [mongo-no-primary.md](mongo-no-primary.md) and escalate as **P1** |
| The failed node won't start, or crashes again | Possible disk, config or data-file problem |
| Stuck in `RECOVERING` over 5 min, or log says `too stale to catch up` | The node fell behind the oplog window and needs a full resync. See [mongo-replication-lag.md](mongo-replication-lag.md) |
| `oom=true` | Capacity problem, not a one-off |
| More than one member down | The next failure takes writes down completely |

**Include in the escalation:**
- `rs.status()` output from a surviving node
- `docker inspect` exit code and OOM flag (Diagnosis step 4)
- The election log lines (Diagnosis step 5)
- Timeline: time of failure, time of new primary, actions taken and their times

---

## Prevention

- **Give mongod time to shut down cleanly.** Set `stop_grace_period: 30s` on each node in `docker-compose.yml` (or use `docker stop -t 30`). In the drill, `docker stop` hit Docker's default 10 s limit and force-killed mongo1 (exit 137) after it had already handed over.
- **For planned maintenance, step down first.** Run `rs.stepDown()` on the primary before patching or restarting it. It's a clean handover in milliseconds instead of a 10 s election.
- **Understand the election timeout trade-off.** `settings.electionTimeoutMillis` (default 10000) is why a crash costs about 10 s of writes. Lowering it speeds up failover but causes unneeded elections on an unstable network. Keep the default unless there's a measured reason to change it.
- **Alert on it.** Add alerts for **primary changed** and **member down/unhealthy** (week 7), each linking to this runbook.
- **Make apps tolerate failover.** Use retryable writes (on by default in modern drivers), `w: "majority"`, and safe-to-retry operations, e.g. upserts on a business key instead of blind inserts. That handles the "error, but the write was saved" case.
- **Keep three or more voting members** so one failure never takes away the majority.

---

## Lab evidence (2026-10-14 and 2026-10-15)

Load: `load_gen.py write`, about 100 inserts/s with `w: "majority"`, during both tests.

| | Part A: `docker stop mongo1` (clean) | Part B: `docker kill mongo2` (crash) |
|---|---|---|
| Failure at (EAT) | 23:57:15.833 | 06:27:55.594 |
| How the election started | `step up request` (handover) | `no PRIMARY in election timeout period` |
| New primary at (EAT) | 23:57:15.908 | 06:28:06.043 |
| **Write outage** | **75 ms** | **about 11 s** (0 ok/s from 06:27:56 to 06:28:06) |
| Errors seen by app | 0 | 4 (`AutoReconnect`, `ServerSelectionTimeoutError`) |
| New primary | mongo2 | mongo3 |
| Container exit code | 137 (stop grace period ran out) | 137 (killed) |
| Acknowledged writes lost | **0** (expected ≥ 236,440, found 236,454\*) | **0** (expected ≥ 262,267, found 262,268) |
| Failed node restarted (EAT) | 00:12:44, rejoined as SECONDARY | 06:30:18, rejoined as SECONDARY |
| Nodes in sync afterwards | all 3 at 236,454 | all 3 at 262,268 |

\* The 14 extra documents in Part A came from a bug in `load_gen.py`'s final total, which was fixed in commit `4bfa387`. In Part B, the 1 extra document is a write that reported an error but was actually saved.
