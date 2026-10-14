# Stack A: MongoDB replica set

Three `mongod` 7.0 nodes in replica set `rs0`. Uses about 1.5 GB of RAM.

| Container | Address        | Role at start         |
|-----------|----------------|-----------------------|
| mongo1    | mongo1:27017   | usually PRIMARY       |
| mongo2    | mongo2:27018   | SECONDARY             |
| mongo3    | mongo3:27019   | SECONDARY             |

Each node uses its own port, so the same addresses work inside Docker and from Ubuntu.

## Start, check, stop

```bash
cd ~/db-ops-lab/stacks/mongo-replicaset
docker compose up -d                 # start; mongo-init initiates rs0 the first time
docker logs mongo-init               # should end with "PRIMARY is mongo1:27017"
docker stats --no-stream             # memory per container

docker compose down                  # stop (data stays in the volumes)
docker compose down -v               # stop AND wipe all data
```

## Connect

```bash
# From Ubuntu (needs the /etc/hosts line from the main README)
mongosh "mongodb://mongo1:27017,mongo2:27018,mongo3:27019/?replicaSet=rs0"

# Always works, without the hosts line
docker exec -it mongo1 mongosh --port 27017
```

## Load sample data

```bash
cd ~/db-ops-lab
python scripts/load_gen.py seed                  # 100,000 orders into shop.orders
python scripts/load_gen.py write --rate 100      # steady writes, 1 status line per second
```

## Health commands (learn these by heart)

```js
rs.status()                          // member states, health, optimes
rs.conf()                            // configuration, priorities, votes
db.hello()                           // who is primary?
rs.printSecondaryReplicationInfo()   // replication lag per secondary
rs.printReplicationInfo()            // oplog size and window
db.serverStatus().connections        // connection counts
```

Quick one-liner for member states:

```js
rs.status().members.map(m => `${m.name}  ${m.stateStr}  health=${m.health}`)
```

## Week 1 drills

Keep `load_gen.py write` running in a second terminal for every drill. It shows when writes fail and when they come back. Time each drill.

### 1. Primary down
```bash
docker stop mongo1                   # if mongo1 is primary
```
- Watch: how many seconds of `err/s` before a new primary appears in the load_gen output?
- Check `rs.status()` from another node: `docker exec -it mongo2 mongosh --port 27018`
- Recover: `docker start mongo1`. It rejoins as SECONDARY. Why doesn't it become primary again?
- Write `runbooks/mongo-primary-down.md`

### 2. Secondary lagging / offline
```bash
docker pause mongo3
python scripts/load_gen.py seed --count 50000    # write while mongo3 is frozen
docker unpause mongo3
```
- Before unpausing, look at `rs.printSecondaryReplicationInfo()`. After unpausing, watch the lag drop to 0.
- `rs.printReplicationInfo()` shows the oplog window. What happens if a node is offline longer than that?
- Write `runbooks/mongo-replication-lag.md`

### 3. No primary (lost majority)
```bash
docker stop mongo2 mongo3
```
- The remaining node steps down to SECONDARY and every write fails. Why? (2 of 3 votes are needed)
- Reads still work: `db.getMongo().setReadPref("secondaryPreferred")`
- Recover: `docker start mongo2 mongo3`
- Write `runbooks/mongo-no-primary.md`. Target: fixed in under 5 minutes from memory.

### 4. Planned failover (maintenance)
```js
rs.stepDown(60)                      // run on the primary
```
- This is how you move the primary off a node before patching it. Compare the error count with drill 1.

## Done when

- You can explain elections, majority and the oplog window aloud in 2 minutes.
- You can fix "no primary" in under 5 minutes without notes.
- The three runbooks are committed.
