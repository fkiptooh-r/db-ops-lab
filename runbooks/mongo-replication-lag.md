# Runbook: MongoDB replication lag

| | |
|---|---|
| **Severity** | **P3:** one secondary behind, writes normal. **P2:** lag above 50% of the oplog window, or still growing after 15 min. **P1:** writes hang (a majority of nodes can't confirm writes). |
| **Owner** | L2 DB Support |
| **Applies to** | Replica set `rs0` (Stack A) |
| **Last tested** | 2026-10-19 |
| **Time to resolve** | Once the cause is removed, secondaries caught up within about 2 s in the lab (from 36 s and about 220 s of lag). Total incident time is usually set by how fast you find the cause. |

**Nodes**

| Container | Address | Connect |
|---|---|---|
| mongo1 | mongo1:27017 | `docker exec -it mongo1 mongosh --port 27017` |
| mongo2 | mongo2:27018 | `docker exec -it mongo2 mongosh --port 27018` |
| mongo3 | mongo3:27019 | `docker exec -it mongo3 mongosh --port 27019` |

> The primary can be any of the three. **Always check roles first** with `scripts/rs_roles.sh`, and never lock or restart a node until you know its role.

---

## Symptoms

- `scripts/rs_lag.sh` shows a secondary with `lag=` well above 2 s, and growing.
- Reads from secondaries (`readPreference=secondary` / `secondaryPreferred`) return **old data**: "I saved it but I can't see it".
- In the worst case, writes **hang with no error**: the app's write rate drops to 0 and requests time out, yet every node looks healthy.
- (Week 7 monitoring) alert: **replication lag high**.

## Impact

| Situation | Writes | Reads | Data at risk |
|---|---|---|---|
| **One secondary behind** | Normal: primary + the other secondary make the majority | Stale on the lagging secondary | None now, but redundancy is reduced: if the primary dies, the lagging node is a poor failover target |
| **One secondary offline** | Normal | Lagging node can't serve reads | Same as above. If it stays offline longer than the **oplog window**, it can't catch up and needs a full resync |
| **Both secondaries behind** | **Hang.** `w: "majority"` waits for 2 of 3 nodes, and only the primary has the write | Stale on both secondaries | No data loss, but a **silent write outage**: no errors and all nodes "healthy" |

---

## Diagnosis

### 1. Which node is behind, and by how much?

```bash
cd ~/db-ops-lab
scripts/rs_roles.sh
scripts/rs_lag.sh --watch 1
```
- **Normal:** every secondary `lag=0s` to `2s`.
- **This incident:** one or more secondaries with lag growing about 1 s per second (it's stuck), or high but falling (it's catching up; just watch it).
- `lag=unknown (down)`: the node is unreachable, so treat it as an offline secondary (step 4). If it was the primary that went down, use [mongo-primary-down.md](mongo-primary-down.md).

Confirm from the **primary** (use its name and port from `rs_roles.sh`):
```js
rs.printSecondaryReplicationInfo()
```
Expected for a lagging node: `replLag: 'N secs (…) behind the primary'`.

### 2. Is the app affected? Are writes moving?

```bash
python scripts/load_gen.py write     # or the app's own write metrics
```

| Writes | Lag | Meaning | Go to |
|---|---|---|---|
| Normal | One secondary growing | Slow or blocked secondary | Step 3 |
| Normal | One secondary `unknown (down)` | Secondary offline | Step 4 |
| **0 ok/s, 0 errors** | **Two secondaries growing** | Majority can't confirm writes. **P1** | Step 3 on **both**, fix right away |
| **0 ok/s, 0 errors** | **About 0 everywhere** | Not lag. **The primary itself is blocked** (nothing new is written, so secondaries have nothing to fall behind on) | Step 3 on the **primary** |

### 3. Why is the node behind?

Run on the node you're investigating:
```bash
docker exec <node> mongosh --quiet --port <port> --eval 'db.adminCommand({ currentOp: 1 }).fsyncLock'
```
- `true`: **the node is fsync-locked** (blocking all writes), usually from a disk-snapshot backup that wasn't unlocked. Its log repeats `WARNING: instance is locked, blocking all writes … remember to unlock the instance using fsyncUnlock()` every minute:
  ```bash
  docker logs --since 30m <node> 2>&1 | grep -E 'CMD fsync|instance is locked|fsyncUnlock' | tail -5
  ```
- No output, or `false`: not locked. Check the other common causes:

| Check | Command | Look for |
|---|---|---|
| CPU or memory starved | `docker stats --no-stream` | the lagging node near 100% CPU or at its memory limit |
| Long-running work on the node | `docker exec <node> mongosh --quiet --port <port> --eval 'db.currentOp({ secs_running: { $gt: 10 } }).inprog.map(o => o.op + " " + o.ns + " " + o.secs_running + "s")'` | index builds or big queries hogging the secondary |
| Sync source | `rs.status().members.map(m => m.name + " syncs from " + m.syncSourceHost)` | a **secondary** with an empty sync source (the primary's is always empty), or one syncing through another lagging node |
| Write burst on the primary | the app's or load_gen's write rate | a batch job writing faster than the secondaries can apply |
| Errors on the node | `docker logs --tail 50 <node>` | disk errors, `too stale` |

### 4. Secondary offline: how long until it can't catch up?

On the **primary**:
```js
rs.printReplicationInfo()
```
- `configured oplog size` / `log length start to end` is the **oplog window**: how far back the primary's change log goes.
- **Headroom = window − time the node has been offline.** While headroom is positive, the node catches up by itself when it returns. If it reaches zero, the node is **too stale** and needs a full resync (escalate).
- Lab value: 48,640 MB of oplog (the default is 5% of free disk, capped at 50 GB), so the window is far longer than any drill. Production oplogs are often much smaller.

Why it's offline:
```bash
docker ps -a --format '{{.Names}}  {{.Status}}' | grep mongo     # Up / Paused / Exited
```

---

## Resolution

### A. Node is fsync-locked

1. **Check before unlocking.** A lock is often deliberate, for a backup snapshot in progress. Confirm with whoever runs backups that the snapshot has finished. Unlocking mid-snapshot makes the backup inconsistent.
2. Unlock:
   ```bash
   docker exec <node> mongosh --quiet --port <port> --eval 'db.fsyncUnlock()'
   ```
   Expected: `lockCount: Long('0')`. Locks stack, so if it shows 1 or more, run it again until it's 0.
3. If **both secondaries** are locked (writes hang), unlock both straight away. This is P1.

### B. Secondary offline

1. Bring it back, depending on its state:
   ```bash
   docker unpause <node>    # if Paused
   docker start <node>      # if Exited
   ```
2. Watch `scripts/rs_lag.sh --watch 1`. Expected: `SECONDARY`, then lag falls to 0.
3. If it stays `RECOVERING`, or logs say `too stale`, **escalate** for a resync. Don't delete data files yourself.

### C. The primary is blocked (writes hang, lag about 0)

1. Check whether the **primary** is locked (step 3 command against the primary). If `true`, confirm no backup is running there, then `db.fsyncUnlock()` on the primary.
2. If it isn't locked, check its CPU, disk and long-running operations (step 3 table). If the cause isn't clear within 5 minutes, escalate as **P1**.

### D. Resource or workload cause

1. Long-running operation on a secondary: identify it with `db.currentOp(...)`. Agree with its owner before stopping it with `db.killOp(<opid>)`.
2. CPU, memory or disk too small, or a write burst from a batch job: escalate. This is a capacity change, not something to fix during the incident.

---

## Verification

### 1. Lag back to normal on every secondary

```bash
scripts/rs_lag.sh
```
Expected: every secondary `lag=0s` to `2s`, and stable over a few runs.

### 2. No node still locked

```bash
for c in mongo1:27017 mongo2:27018 mongo3:27019; do
  printf '%s fsyncLock=' "$c"
  docker exec "${c%:*}" mongosh --quiet --port "${c#*:}" --eval 'print(db.adminCommand({ currentOp: 1 }).fsyncLock === true)'
done
```
Expected: `false` on all three.

### 3. Every node has the same data

```bash
for c in mongo1:27017 mongo2:27018 mongo3:27019; do
  printf '%s  ' "$c"
  docker exec "${c%:*}" mongosh --quiet --port "${c#*:}" \
    --eval 'db.getMongo().setReadPref("secondaryPreferred"); db.getSiblingDB("shop").orders.countDocuments()'
done
```
Expected: three equal numbers (stop writes first, or run it twice).

### 4. Writes flowing

`python scripts/load_gen.py write`. Expected: steady `ok/s` and `err/s` = 0.

---

## Escalation

Escalate to **L3 DBA** when:

| Condition | Severity |
|---|---|
| Writes hang and the cause isn't removed within 5 min | P1 |
| Lag above 50% of the oplog window, or growing for over 15 min | P2 |
| Node `too stale` or stuck in `RECOVERING`, so it needs a resync | P2 |
| Cause is capacity (CPU, disk, memory) or a recurring batch job | P3, follow-up |
| You can't confirm whether an fsync lock belongs to a running backup | Ask the backup owner before acting |

**Include:** `rs.printSecondaryReplicationInfo()` and `rs.printReplicationInfo()` from the primary, `rs_lag.sh` output over time, the fsyncLock check, `docker stats`, the relevant log lines, and a timeline.

---

## Prevention

- **Alert on lag, not only on node health.** In the drill every node stayed "healthy" while writes hung. Alert at **lag > 30 s (warning)** and **> 120 s (critical)**, and on any node reporting `fsyncLock: true` (week 7).
- **Give writes a timeout.** Use `w: "majority"` with `wtimeoutMS` (e.g. 5000) so the app gets an error it can report, instead of hanging for as long as the secondaries lag.
- **Make backups unlock themselves.** Any script that runs `fsyncLock` must run `fsyncUnlock` in a cleanup step that always executes, even on failure. Or use `mongodump` or storage snapshots that don't need a manual lock (week 2).
- **Size and watch the oplog window.** Keep it at 24–72 h of peak writes, and alert if it shrinks below 24 h (`rs.printReplicationInfo()`).
- **Secondaries match the primary.** Same CPU, memory and disk class, because any secondary may become primary.
- **Keep heavy work off secondaries.** Run reports and index builds when it's quiet, or on a dedicated hidden member.

---

## Lab evidence

Load: `load_gen.py write`, about 100 inserts/s with `w: "majority"`. Times in EAT (log times are UTC + 3).

| | A: one secondary locked | B: one secondary offline | C: both secondaries locked | C as run: primary + one secondary locked |
|---|---|---|---|---|
| Date | 2026-10-19 | 2026-10-19 | 2026-10-16 | 2026-10-19 |
| Primary at the time | mongo1 | mongo1 | mongo2 | **mongo1 (locked)** |
| Locked / paused | mongo3 | mongo3 (`docker pause`) | mongo3 + mongo1 | mongo3 + mongo1 |
| Start | 15:01:04 | DOWN detected 15:05:55 (about 10 s after pause) | 12:36:35 | 15:07:56; repeated 15:21:14 |
| Max lag | about 220 s (locked 3 min 41 s; lag grows 1 s per second) | `unknown (down)` | grew on both | **about 0–1 s** (nothing new to replicate) |
| Fixed | 15:04:45 (`fsyncUnlock`) | `docker unpause` | 12:36:52 (both unlocked) | 15:09:27; repeat 15:21:43 |
| Catch-up after fix | about 2 s | seconds | about 2 s | immediate |
| App writes during | normal, 0 errors | normal, 0 errors | **0 ok/s, 0 errors for about 16 s** | **0 ok/s, 0 errors for about 29 s** (repeat run) |
| Did `rs_roles.sh` show a problem? | No | Yes (unreachable) | **No** | **No** |

All three nodes ended with the same count (505,844 documents). No writes were lost in any part.

**Key lesson:** "writes hang, no errors, all nodes healthy" has two different causes. The lag tells them apart. **High lag** means the secondaries can't confirm writes. **Lag about 0** means the primary itself is blocked.
