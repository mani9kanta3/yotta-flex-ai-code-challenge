"""
Print every ATP number for one SKU on one channel, at any time.

Run:  python -m scripts.show_atp --sku SKU-000001 --channel WEB
      python -m scripts.show_atp --sku SKU-000001 --channel WEB --at "2026-03-01 10:00:00"

The API isn't built yet, so this is how to look at the numbers for now.
"""

import argparse
from datetime import datetime

from app import atp, config, db


def main():
    parser = argparse.ArgumentParser(description="Show ATP for one SKU.")
    parser.add_argument("--sku", required=True)
    parser.add_argument("--channel", default="WEB", choices=["WEB", "APP", "MARKETPLACE", "STORE"])
    parser.add_argument("--at", help="a time like 2026-03-01 10:00:00, default the end of the data")
    args = parser.parse_args()
    t = datetime.fromisoformat(args.at) if args.at else config.AS_OF

    connection = db.get_connection()
    try:
        with connection.cursor() as cursor:
            positions = atp.get_positions(cursor, args.sku, args.channel, t)
    finally:
        connection.close()

    print(f"{args.sku} on {args.channel} at {t}")
    print(f"{'location':12}{'type':11}{'on_hand':>8}{'blocked':>8}{'reserved':>9}{'inbound':>8}"
          f"{'atp':>6}{'safety':>7}{'buffer':>7}{'promisable':>11}")
    for p in positions:
        print(f"{p['location_id']:12}{p['location_type']:11}{p['on_hand']:>8}{p['blocked']:>8}"
              f"{p['reserved']:>9}{p['inbound']:>8}{p['atp']:>6}{p['safety_stock']:>7}"
              f"{p['buffer_units']:>7}{p['promisable']:>11}")
    print(f"network promisable: {atp.network_promisable(positions)}")


if __name__ == "__main__":
    main()
