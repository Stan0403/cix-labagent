"""USB serial voltage ingestion. This reads data; it NEVER switches a physical circuit."""
import argparse
import json
import math
import time

import httpx

from labagent.config import Settings


def main():
    parser = argparse.ArgumentParser(description="Forward calibrated device voltage JSON lines to LabAgent")
    parser.add_argument("--port", required=True, help="COM3 or /dev/ttyACM0")
    parser.add_argument("--session", required=True, help="Target LabAgent session ID, currently at measurement step")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    try:
        import serial
    except ImportError:
        raise SystemExit("Install .[serial] first")
    settings = Settings.load()
    headers = {"Authorization": "Bearer " + settings.access_token} if settings.access_token else {}
    with serial.Serial(args.port, args.baud, timeout=1) as device, httpx.Client(timeout=10, headers=headers, trust_env=False) as client:
        while True:
            line = device.readline(2048)
            if not line:
                continue
            try:
                data = json.loads(line)
                value = float(data["voltage"])
                if not math.isfinite(value) or data.get("unit") != "V":
                    raise ValueError()
            except (ValueError, KeyError, TypeError):
                print("Rejected malformed device record; expected {\"voltage\":1.25,\"unit\":\"V\"}")
                continue
            try:
                response = client.post(args.url.rstrip("/") + f"/api/sessions/{args.session}/sensor",
                                       json={"value": value, "unit": "V", "device": args.port})
                response.raise_for_status()
                print(response.json()["message"])
            except httpx.HTTPError as exc:
                print(f"Ingestion failed: {type(exc).__name__}; check service, token and current step")
            time.sleep(0.5)


if __name__ == "__main__":
    main()
