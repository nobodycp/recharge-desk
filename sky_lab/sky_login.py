#!/usr/bin/env python3
"""Login to Sky Sales with IP-bound session management."""

import json
import os
import sys

import _lab_env  # noqa: F401

from phone_refresh.providers.sky_sales_client import (
    SkySalesClient,
    SkySalesError,
    load_dotenv_sky,
)


def main() -> int:
    load_dotenv_sky()
    user = os.environ.get("SKY_SALES_USER", "")
    password = os.environ.get("SKY_SALES_PASSWORD", "")
    otp = os.environ.get("SKY_SALES_OTP", "")

    if not user or not password:
        print(
            json.dumps(
                {"success": False, "message": "ضع SKY_SALES_USER و SKY_SALES_PASSWORD في sky_lab/.env"},
                ensure_ascii=False,
            )
        )
        return 1

    try:
        saved = SkySalesClient.load_session_file()
        client = saved if saved else SkySalesClient()
        client.ensure_authenticated(user, password, otp)
        print(
            json.dumps(
                {
                    "success": True,
                    "message": "جلسة جاهزة",
                    "user": user,
                    "eot_user_id": client.session.eot_user_id,
                    "bound_ip": client.session.bound_ip,
                },
                ensure_ascii=False,
            )
        )
        return 0
    except SkySalesError as exc:
        print(json.dumps({"success": False, "message": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
