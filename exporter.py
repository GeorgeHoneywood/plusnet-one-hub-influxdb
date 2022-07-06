#!/usr/bin/env python3

import hashlib
import logging
import os
import sys
import time
import requests
import argparse

from bs4 import BeautifulSoup as bs
from datetime import datetime
from influxdb import InfluxDBClient

from dehumanise import human2bytes


class PlusnetHubOne:
    CONN_INFO_SUFFIX = "?active_page=9121"
    session = requests.Session()

    def __init__(self, password, router_ip):
        self.password = password
        self.base_url = f"http://{router_ip}/index.cgi"

    def login(self):
        # will redirect to the login page first, if we aren't authenticated
        login_page = self.session.get(self.base_url + self.CONN_INFO_SUFFIX)

        if "No more than 100 sessions at a time are allowed. Please wait until open sessions expire." in login_page.text:
            logging.fatal("too many sessions open, please wait")
            exit(1)

        login_soup = bs(login_page.text, features="html.parser")

        auth_key = login_soup.find("input", {"name": "auth_key"})["value"]
        post_token = login_soup.find("input", {"name": "post_token"})["value"]

        # the md5_pass value is the md5'd concatenation of the plaintext password and the auth_key (retrieved from the login form)
        md5_pass = self.password + auth_key
        md5_pass = hashlib.md5(md5_pass.encode()).hexdigest()

        form_data = {
            "active_page": "9148",
            "mimic_button_field": "submit_button_login_submit: ..",
            "post_token": post_token,
            "md5_pass": md5_pass,
            "auth_key": auth_key,
        }

        # authorizes our cookie
        self.session.post(self.base_url, data=form_data)

    def collect_stats(self):
        conn_info_page = self.session.get(self.base_url + self.CONN_INFO_SUFFIX)
        conn_info_soup = bs(conn_info_page.text, features="html.parser")

        cookie = self.session.cookies.get_dict()["rg_cookie_session_id"]
        # hacky way to check if we are logged in
        if "password protected" in conn_info_page.text:
            logging.warning(f"cookie expired, must login again: {cookie}")
            self.login()
            return self.collect_stats()

        logging.info(f"authorized successfully with cookie: {cookie}")

        stats = conn_info_soup.find(
            "td", text="Data Transmitted/Received:"
        ).next_sibling.text.split("/")
        transmitted, received = [human2bytes(value.strip()) for value in stats]

        logging.info(f"transmitted: {transmitted} bytes, received: {received} bytes")

        firmware_updated = conn_info_soup.find(id="footer").text.split("Last updated ")[
            1
        ]
        firmware_updated = datetime.strptime(firmware_updated, "%d/%m/%y")

        return {
            "transmitted": transmitted,
            "received": received,
            "firmware_updated": time.mktime(firmware_updated.timetuple()),
        }


def main():
    parser = argparse.ArgumentParser(
        description="collect stats from a Plusnet Hub One router, and send them to InfluxDB",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument(
        "--router-ip",
        type=str,
        default="192.168.1.254",
        help="router IP address",
    )
    parser.add_argument(
        "--router-password",
        type=str,
        required=True,
        help="router admin password",
    )
    parser.add_argument(
        "--influxdb-url",
        type=str,
        required=True,
        help="influxdb URL",
    )
    parser.add_argument(
        "--influxdb-database",
        type=str,
        default="plusnet_router",
        help="influxdb database to write to",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=15,
        help="stats collection interval in seconds",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        help="be verbose",
        dest="verbosity",
        action="store_const",
        const=logging.INFO,
    )

    args = parser.parse_args()

    logging.basicConfig(level=args.verbosity, format="%(levelname)s: %(message)s")

    router = PlusnetHubOne(args.router_password, args.router_ip)
    # not strictly necessary, but prevents a warning message from being printed at startup
    router.login()
    print(
        f"authorized for router at {args.router_ip}, with password {len(args.router_password) * '*'}"
    )

    client = InfluxDBClient(host=args.influxdb_url)
    print(f"connected to influxdb at {args.influxdb_url}")

    client.switch_database(args.influxdb_database)

    while True:
        start = time.perf_counter()

        try:
            stats = router.collect_stats()

            client.write_points(
                [
                    {
                        "measurement": "data_stats",
                        "time": datetime.utcnow(),
                        "fields": stats,
                    }
                ]
            )
        except Exception as e:
            logging.exception(e)

        logging.info(
            f"took {time.perf_counter() - start:0.4f} seconds to collect stats"
        )
        time.sleep(args.interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nexiting!")
        try:
            sys.exit(0)
        except SystemExit:
            os._exit(0)
