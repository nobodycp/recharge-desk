#!/usr/bin/env python3
"""Refresh a Sky Sales subscriber SIM via HTTP API (no browser)."""

import argparse
import json
import sys

import _lab_env  # noqa: F401

from phone_refresh.providers.sky_sales_client import execute_refresh


def main() -> int:
    parser = argparse.ArgumentParser(description="Sky Sales SIM refresh")
    parser.add_argument("phone", help="MSISDN e.g. 0555544071")
    args = parser.parse_args()

    result = execute_refresh(args.phone)
    print(json.dumps(result, ensure_ascii=False))
    if result.get("otp_required"):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
