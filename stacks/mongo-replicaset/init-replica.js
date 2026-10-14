// Initiates replica set rs0. Safe to run more than once.
// Run by the mongo-init service, or by hand:
//   mongosh "mongodb://mongo1:27017/?directConnection=true" init-replica.js

const config = {
  _id: "rs0",
  members: [
    { _id: 0, host: "mongo1:27017" },
    { _id: 1, host: "mongo2:27018" },
    { _id: 2, host: "mongo3:27019" },
  ],
};

let initiated = true;
try {
  rs.status();
} catch (e) {
  if (e.codeName !== "NotYetInitialized") throw e;
  initiated = false;
}

if (initiated) {
  print("rs0 is already initiated, nothing to do.");
} else {
  printjson(rs.initiate(config));
}

// Wait up to 60 s for an election to finish.
for (let i = 0; i < 60; i++) {
  const primary = rs.status().members.find((m) => m.stateStr === "PRIMARY");
  if (primary) {
    print(`PRIMARY is ${primary.name}`);
    quit(0);
  }
  sleep(1000);
}
print("No PRIMARY after 60 s. Check rs.status().");
quit(1);
