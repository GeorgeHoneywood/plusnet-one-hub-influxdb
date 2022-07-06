# influxdb exporter for the Plusnet Hub One

This is a little python script to take data from a Plusnet Hub One and export it to InfluxDB.

## Install dependencies (in a `virtualenv`)

```sh
virtualenv .venv
source ./.venv/bin/activate

pip install -r requirements.txt
```

## Usage

```sh 
➜ ./exporter.py --help
usage: exporter.py [-h] [--router-ip ROUTER_IP] --router-password ROUTER_PASSWORD --influxdb-url INFLUXDB_URL [--influxdb-database INFLUXDB_DATABASE] [--interval INTERVAL] [-v]

collect stats from a Plusnet Hub One router, and send them to InfluxDB

options:
  -h, --help            show this help message and exit
  --router-ip ROUTER_IP
                        router IP address (default: 192.168.1.254)
  --router-password ROUTER_PASSWORD
                        router admin password (default: None)
  --influxdb-url INFLUXDB_URL
                        influxdb URL (default: None)
  --influxdb-database INFLUXDB_DATABASE
                        influxdb database to write to (default: plusnet_router)
  --interval INTERVAL   stats collection interval in seconds (default: 15)
  -v, --verbose         be verbose (default: None)

```
