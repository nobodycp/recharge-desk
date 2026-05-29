#!/usr/bin/env python3
"""استخراج TOTP secret من رابط otpauth:// (نص QR) أو صورة QR."""

import argparse
import json
import re
import sys


def secret_from_otpauth(uri: str) -> str:
    uri = uri.strip()
    m = re.search(r"[?&]secret=([A-Za-z2-7=]+)", uri, re.I)
    if not m:
        raise ValueError("لم أجد secret= في الرابط")
    return m.group(1).upper().replace("=", "")


def secret_from_qr_image(path: str) -> str:
    try:
        from pyzbar.pyzbar import decode
        from PIL import Image
    except ImportError as exc:
        raise SystemExit("pip install pyzbar pillow") from exc

    for item in decode(Image.open(path)):
        text = item.data.decode("utf-8", errors="ignore")
        if "otpauth://" in text:
            return secret_from_otpauth(text)
    raise ValueError("لا يوجد otpauth في صورة QR")


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract TOTP secret for sky_lab/.env")
    parser.add_argument("input", help="otpauth:// URI أو مسار صورة QR")
    args = parser.parse_args()

    try:
        if args.input.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
            secret = secret_from_qr_image(args.input)
        else:
            secret = secret_from_otpauth(args.input)
        print(
            json.dumps(
                {
                    "success": True,
                    "SKY_SALES_TOTP_SECRET": secret,
                    "hint": "أضف السطر في sky_lab/.env",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except Exception as exc:
        print(json.dumps({"success": False, "message": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
