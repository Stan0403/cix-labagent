"""Save one clean training frame from a board camera without running inference."""

import argparse
import time
from pathlib import Path

import cv2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("device", help="Video capture device, e.g. /dev/video4")
    parser.add_argument("output", type=Path, help="New .jpg file")
    args = parser.parse_args()
    if args.output.suffix.lower() not in {".jpg", ".jpeg"} or args.output.exists():
        parser.error("output must be a new JPEG path")

    camera = cv2.VideoCapture(args.device, cv2.CAP_V4L2)
    try:
        if not camera.isOpened():
            raise RuntimeError(f"Cannot open capture device {args.device}")
        camera.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        camera.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
        camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
        frame = None
        warm_until = time.monotonic() + 1.0
        for _ in range(40):
            ok, candidate = camera.read()
            if ok and candidate is not None:
                frame = candidate
            if frame is not None and time.monotonic() >= warm_until:
                break
        if frame is None:
            raise RuntimeError("Camera did not return a frame")
    finally:
        camera.release()

    ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
    if not ok:
        raise RuntimeError("Cannot encode captured frame")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("xb") as output:
        output.write(jpeg.tobytes())
    height, width = frame.shape[:2]
    print(f"Captured {width}x{height}, {len(jpeg)} bytes: {args.output}")


if __name__ == "__main__":
    main()
