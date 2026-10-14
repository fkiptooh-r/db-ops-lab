#!/usr/bin/env python3
"""Generate realistic order data for the lab.

Two modes:
  seed   Bulk-insert N orders (default 100,000), then exit.
  write  Insert orders continuously and print one status line per second.
         Leave it running while you break the cluster to see the impact:
         failed writes, which node is primary, and when writes recover.

Examples:
  python scripts/load_gen.py seed
  python scripts/load_gen.py seed --count 50000
  python scripts/load_gen.py write --rate 200 --clients 4
  python scripts/load_gen.py write --rate 0          # as fast as possible

Set MONGO_URI or pass --uri to point it at another cluster.
"""

import argparse
import os
import random
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

from pymongo import MongoClient, WriteConcern
from pymongo.errors import PyMongoError

DEFAULT_URI = "mongodb://mongo1:27017,mongo2:27018,mongo3:27019/?replicaSet=rs0"

REGIONS = ["Nairobi", "Mombasa", "Kisumu", "Nakuru", "Eldoret", "Thika", "Machakos"]
PAYMENT_METHODS = ["mpesa"] * 6 + ["card"] * 3 + ["bank"]
STATUSES = ["pending", "paid", "shipped", "delivered", "cancelled"]
PRODUCTS = [
    ("SKU-1001", "Maize flour 2kg", 210),
    ("SKU-1002", "Cooking oil 1L", 380),
    ("SKU-1003", "Sugar 1kg", 190),
    ("SKU-1004", "Tea leaves 500g", 320),
    ("SKU-1005", "Rice 2kg", 450),
    ("SKU-1006", "Milk 500ml", 65),
    ("SKU-1007", "Bread 400g", 70),
    ("SKU-1008", "Phone charger", 850),
    ("SKU-1009", "Solar lamp", 2400),
    ("SKU-1010", "Exercise book", 50),
]


def make_order(rng, now=None):
    items = []
    for sku, name, price in rng.sample(PRODUCTS, rng.randint(1, 4)):
        qty = rng.randint(1, 5)
        items.append({"sku": sku, "name": name, "qty": qty, "unit_price": price})
    created = now or datetime.now(timezone.utc) - timedelta(minutes=rng.randint(0, 60 * 24 * 90))
    return {
        "customer_id": f"CUST-{rng.randint(1, 20000):05d}",
        "region": rng.choice(REGIONS),
        "status": rng.choice(STATUSES),
        "payment_method": rng.choice(PAYMENT_METHODS),
        "items": items,
        "total_kes": sum(i["qty"] * i["unit_price"] for i in items),
        "created_at": created,
    }


def get_collection(uri, w):
    client = MongoClient(uri, serverSelectionTimeoutMS=5000, appname="load_gen")
    wc = WriteConcern(w=int(w) if str(w).isdigit() else w)
    return client, client["shop"].get_collection("orders", write_concern=wc)


def current_primary(client):
    # With a replica set URI the driver tracks the primary itself. With
    # directConnection=true it doesn't, so ask the node we're connected to.
    if client.topology_description.topology_type_name != "Single":
        primary = client.primary
        return f"{primary[0]}:{primary[1]}" if primary else "NONE"
    try:
        return client.admin.command("hello").get("primary", "NONE")
    except PyMongoError:
        return "unreachable"


def seed(args):
    client, orders = get_collection(args.uri, args.w)
    rng = random.Random(42)
    start = time.time()
    done = 0
    while done < args.count:
        batch = [make_order(rng) for _ in range(min(args.batch, args.count - done))]
        orders.insert_many(batch, ordered=False)
        done += len(batch)
        print(f"\rinserted {done:,} / {args.count:,}", end="", flush=True)
    secs = time.time() - start
    print(f"\ndone in {secs:.1f}s ({done / secs:,.0f} docs/s)")
    print(f"shop.orders now has {orders.estimated_document_count():,} documents")
    client.close()


def write(args):
    client, orders = get_collection(args.uri, args.w)
    stats = {"ok": 0, "err": 0, "last_err": ""}
    lock = threading.Lock()
    stop = threading.Event()
    per_client_rate = args.rate / args.clients if args.rate > 0 else 0

    def worker(seed_value):
        rng = random.Random(seed_value)
        while not stop.is_set():
            t0 = time.time()
            try:
                orders.insert_one(make_order(rng, now=datetime.now(timezone.utc)))
                with lock:
                    stats["ok"] += 1
            except PyMongoError as e:
                with lock:
                    stats["err"] += 1
                    stats["last_err"] = type(e).__name__
            if per_client_rate:
                time.sleep(max(0.0, 1 / per_client_rate - (time.time() - t0)))

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(args.clients)]
    for t in threads:
        t.start()

    print("time      ok/s  err/s  primary          last error   (Ctrl+C to stop)")
    total_ok = total_err = 0
    try:
        while True:
            time.sleep(1)
            with lock:
                ok, err, last_err = stats["ok"], stats["err"], stats["last_err"]
                stats.update(ok=0, err=0, last_err="")
            total_ok += ok
            total_err += err
            primary = current_primary(client)
            stamp = datetime.now().strftime("%H:%M:%S")
            print(f"{stamp}  {ok:5d}  {err:5d}  {primary:15s}  {last_err}", flush=True)
    except KeyboardInterrupt:
        stop.set()
        print(f"\nstopped. total ok={total_ok:,} failed={total_err:,}")
        client.close()


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--uri", default=os.environ.get("MONGO_URI", DEFAULT_URI))
    p.add_argument("--w", default="majority", help='write concern: "majority", 1, 0 ... (default: majority)')
    sub = p.add_subparsers(dest="mode", required=True)

    s = sub.add_parser("seed", help="bulk-insert orders and exit")
    s.add_argument("--count", type=int, default=100_000)
    s.add_argument("--batch", type=int, default=1000)

    w = sub.add_parser("write", help="insert continuously and report every second")
    w.add_argument("--rate", type=int, default=100, help="target inserts/s in total, 0 = unlimited (default: 100)")
    w.add_argument("--clients", type=int, default=2, help="parallel writer threads (default: 2)")

    args = p.parse_args()
    try:
        seed(args) if args.mode == "seed" else write(args)
    except PyMongoError as e:
        sys.exit(f"MongoDB error: {e}")


if __name__ == "__main__":
    main()
