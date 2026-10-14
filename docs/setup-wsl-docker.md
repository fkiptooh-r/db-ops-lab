# Laptop setup: WSL2 + Docker

Windows 11, 16 GB RAM. Linux runs in WSL2 (Ubuntu 24.04), containers run in Docker Desktop with the WSL2 engine.

## 1. Cap WSL memory

`C:\Users\hp\.wslconfig`:

```ini
[wsl2]
memory=10GB
processors=4
swap=4GB
```

Apply it from PowerShell: `wsl --shutdown` (or `wsl.exe --shutdown` from inside Ubuntu).

## 2. Docker Desktop

Settings → General → **Use the WSL 2 based engine**.
Settings → Resources → WSL integration → **Ubuntu** on → Apply & restart.

## 3. Tools inside Ubuntu

```bash
sudo apt update
sudo apt install -y gnupg jq python3-pip python3-venv

curl -fsSL https://www.mongodb.org/static/pgp/server-8.0.asc | \
  sudo gpg -o /usr/share/keyrings/mongodb-server-8.0.gpg --dearmor
echo "deb [ arch=amd64,arm64 signed-by=/usr/share/keyrings/mongodb-server-8.0.gpg ] https://repo.mongodb.org/apt/ubuntu noble/mongodb-org/8.0 multiverse" | \
  sudo tee /etc/apt/sources.list.d/mongodb-org-8.0.list
sudo apt update
sudo apt install -y mongodb-mongosh mongodb-database-tools

python3 -m venv ~/labenv
echo 'source ~/labenv/bin/activate' >> ~/.bashrc
source ~/labenv/bin/activate
pip install -r ~/db-ops-lab/scripts/requirements.txt
```

## 4. Hostnames for the replica set

The replica set members are called `mongo1..3`. Ubuntu must resolve those names to `127.0.0.1`.
WSL rewrites `/etc/hosts` at every start, so turn that off first:

```bash
printf '\n[network]\ngenerateHosts = false\n' | sudo tee -a /etc/wsl.conf
echo '127.0.0.1  mongo1 mongo2 mongo3' | sudo tee -a /etc/hosts
```

## 5. Verify

```bash
free -h | head -2              # total ≈ 9.7 GB
which docker                   # /usr/bin/docker
docker run --rm hello-world
docker compose version
mongosh --version
mongodump --version | head -1
jq --version
getent hosts mongo1            # 127.0.0.1  mongo1 mongo2 mongo3
```

Verified on 2026-10-14: Docker Compose v5.6.0, mongosh 2.13.0, database tools 100.19.1, jq 1.7, Python 3.12.3.
