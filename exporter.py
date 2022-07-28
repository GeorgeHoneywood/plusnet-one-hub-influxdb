#!/usr/bin/env python3

import argparse
import functools
import hashlib
import json
import logging
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup as bs
from influxdb import InfluxDBClient

from dehumanise import human2bytes


class PlusnetHubOne:
    CONN_INFO_SUFFIX = "?active_page=9143"
    REBOOT_TIME_PATTERN = re.compile(r"wait = (\d*);")

    session = requests.Session()

    @dataclass(kw_only=True)
    class Stats:
        total_tx: int
        total_rx: int

        firmware_update_datetime: int
        reboot_datetime: int

        data_rate_tx: int
        data_rate_rx: int

        max_data_rate_tx: int
        max_data_rate_rx: int

        noise_margin_tx: float
        noise_margin_rx: float

        line_attenuation_tx: float
        line_attenuation_rx: float

        signal_attenuation_tx: float
        signal_attenuation_rx: float

    def __init__(self, password: str, router_ip: str):
        self.password = password
        self.base_url = f"http://{router_ip}/index.cgi"
        # set timeout for all requests in session
        # see: https://github.com/psf/requests/issues/2011#issuecomment-490050252
        self.session.request = functools.partial(  # type: ignore
            self.session.request,
            timeout=3,
        )

    def login(self) -> None:
        # will redirect to the login page first, if we aren't authenticated
        login_page = self.session.get(self.base_url + self.CONN_INFO_SUFFIX)

        if (
            "No more than 100 sessions at a time are allowed. Please wait until open sessions expire."
            in login_page.text
        ):
            logging.fatal("too many sessions open, please wait")
            exit(1)

        login_soup = bs(login_page.text, features="html.parser")

        auth_key: str = login_soup.find("input", {"name": "auth_key"})["value"]  # type: ignore
        post_token: str = login_soup.find("input", {"name": "post_token"})["value"]  # type: ignore

        # the md5_pass value is the md5'd concatenation of the plaintext password and the auth_key (retrieved from the login form)
        md5_pass: str = self.password + auth_key
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

    def collect_stats(self) -> Stats:
        conn_info_page = self.session.get(self.base_url + self.CONN_INFO_SUFFIX)
        conn_info_soup = bs(conn_info_page.text, features="html.parser")

        cookie = self.session.cookies.get_dict()["rg_cookie_session_id"]
        # hacky way to check if we are logged in
        if "password protected" in conn_info_page.text:
            logging.warning(f"cookie expired, must login again: {cookie}")
            self.login()
            return self.collect_stats()

        logging.info(f"authorized successfully with cookie: {cookie}")

        usage = conn_info_soup.find(
            "td", text="11. Data sent/received:"
        ).next_sibling.text.split("/")
        transmitted, received = [human2bytes(value.strip()) for value in usage]
        logging.info(f"transmitted: {transmitted} bytes, received: {received} bytes")

        firmware_update_string = conn_info_soup.find(
            "td", text="3. Firmware version:"
        ).next_sibling.text.split("Last updated ")[1]
        firmware_update_datetime = datetime.strptime(firmware_update_string, "%d/%m/%y")

        seconds_since_reboot = self.REBOOT_TIME_PATTERN.search(conn_info_page.text).group(1)  # type: ignore
        reboot_datetime = datetime.now() - timedelta(seconds=int(seconds_since_reboot))

        data_rate_tx, data_rate_rx = conn_info_soup.find(
            "td", text="6. Data rate:"
        ).next_sibling.text.split("/")

        max_data_rate_tx, max_data_rate_rx = conn_info_soup.find(
            "td", text="7. Maximum data rate:"
        ).next_sibling.text.split("/")

        noise_margin_tx, noise_margin_rx = conn_info_soup.find(
            "td", text="8. Noise margin:"
        ).next_sibling.text.split("/")

        line_attenuation_tx, line_attenuation_rx = conn_info_soup.find(
            "td", text="9. Line attenuation:"
        ).next_sibling.text.split("/")

        signal_attenuation_tx, signal_attenuation_rx = conn_info_soup.find(
            "td", text="10. Signal attenuation:"
        ).next_sibling.text.split("/")

        return self.Stats(
            total_tx=transmitted,
            total_rx=received,
            firmware_update_datetime=int(
                time.mktime(firmware_update_datetime.timetuple()),
            ),
            reboot_datetime=int(
                time.mktime(reboot_datetime.timetuple()),
            ),
            data_rate_tx=int(data_rate_tx),
            data_rate_rx=int(data_rate_rx),
            max_data_rate_tx=int(max_data_rate_tx),
            max_data_rate_rx=int(max_data_rate_rx),
            noise_margin_tx=float(noise_margin_tx),
            noise_margin_rx=float(noise_margin_rx),
            line_attenuation_tx=float(line_attenuation_tx),
            line_attenuation_rx=float(line_attenuation_rx),
            signal_attenuation_tx=float(signal_attenuation_tx),
            signal_attenuation_rx=float(signal_attenuation_rx),
        )


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
                        "fields": asdict(stats),
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
        sys.exit(0)
