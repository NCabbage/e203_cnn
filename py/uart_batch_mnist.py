#!/usr/bin/env python3
"""Batch MNIST test over the E203 UART RUN protocol, without torch/torchvision."""
import argparse
import csv
import gzip
import struct
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np
import serial


def load_idx(path: Path):
    with gzip.open(path, "rb") as fh:
        magic, count = struct.unpack(">II", fh.read(8))
        if magic == 2051:
            rows, cols = struct.unpack(">II", fh.read(8))
            data = np.frombuffer(fh.read(count * rows * cols), dtype=np.uint8).reshape(count, rows * cols)
            return data
        if magic == 2049:
            return np.frombuffer(fh.read(count), dtype=np.uint8)
        raise ValueError(f"unexpected IDX magic {magic:#x} in {path}")


def read_line(ser, deadline, echo=False):
    while time.time() < deadline:
        line = ser.readline().decode("ascii", errors="ignore").strip()
        if line:
            if echo:
                print(f"  [E203] {line}", flush=True)
            return line
    raise TimeoutError("serial response timeout")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM9")
    ap.add_argument("--baud", type=int, default=57600)
    ap.add_argument("--count", type=int, default=1000)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--skip-version", action="store_true")
    root = Path(__file__).resolve().parents[1]
    ap.add_argument("--data-dir", default=str(root / "dataset" / "mnist"))
    ap.add_argument("--csv", default=str(root / "py" / "batch_result.csv"))
    args = ap.parse_args()

    data_dir = Path(args.data_dir)
    start = args.start
    stop = start + args.count
    images = load_idx(data_dir / "t10k-images-idx3-ubyte.gz")[start:stop]
    labels = load_idx(data_dir / "t10k-labels-idx1-ubyte.gz")[start:stop]
    print(f"MNIST loaded: {len(images)} images [{start}:{stop}] from {data_dir}", flush=True)

    ser = serial.Serial(args.port, args.baud, timeout=2)
    try:
        time.sleep(0.5)
        ser.reset_input_buffer()
        if not args.skip_version:
            print("Waiting for E203 CNN VERSION OK...", flush=True)
            deadline = time.time() + 60
            while True:
                line = read_line(ser, deadline, echo=True)
                if "CNN VERSION OK" in line:
                    break
        else:
            print("Skipping boot banner; waiting 2s for E203 command loop...", flush=True)
            time.sleep(2.0)

        def recover_line():
            ser.write(b"\n")
            ser.flush()
            time.sleep(0.05)
            try:
                ser.reset_input_buffer()
            except Exception:
                pass

        def wait_prefix(command: bytes, prefix: str, timeout: float = 2.0, retries: int = 8):
            for attempt in range(retries):
                ser.write(command)
                ser.flush()
                try:
                    deadline = time.time() + timeout
                    while True:
                        line = read_line(ser, deadline, echo=False)
                        if line.startswith(prefix):
                            return line
                        if line.startswith("UNKNOWN") or line.startswith("ACK"):
                            break
                except TimeoutError:
                    pass
                recover_line()
            raise RuntimeError(f"no {prefix!r} response after {retries} retries")

        rows = []
        for i, (img, label) in enumerate(zip(images, labels), start=1):
            if i <= 20 or i % 100 == 0:
                print(f"[{i}] SEND BEG label={int(label)}", flush=True)
            wait_prefix(f"BEG {int(label)}\n".encode("ascii"), "READY")
            payload = img.tobytes()
            for off in range(0, len(payload), 128):
                chunk = payload[off:off + 128]
                expected = f"ACK {off} {len(chunk)}"
                line = wait_prefix(f"DATA {off} {chunk.hex()}\n".encode("ascii"), expected)
                if line != expected:
                    raise RuntimeError(f"bad ACK: got {line!r}, expected {expected!r}")
            if i <= 20 or i % 100 == 0:
                print(f"[{i}] IMAGE ACKED", flush=True)
            line = wait_prefix(b"END\n", "PRED ", timeout=3.0, retries=4)
            parts = line.split()
            pred, cycles = int(parts[1]), int(parts[3])
            if i <= 20 or i % 100 == 0:
                print(f"[{i}] PRED={pred} cycles={cycles}", flush=True)
            time.sleep(0.05)
            rows.append((i - 1, int(label), pred, cycles))
            if i % 10 == 0 or i == len(images):
                correct = sum(1 for r in rows if r[1] == r[2])
                print(f"[{i:4d}/{len(images)}] correct={correct} accuracy={100.0*correct/i:.2f}%", flush=True)

        ser.write(b"EXIT\n")
        ser.flush()
        time.sleep(0.2)
    finally:
        ser.close()

    correct = sum(1 for r in rows if r[1] == r[2])
    total = len(rows)
    avg_cyc = sum(r[3] for r in rows) / total
    csv_path = Path(args.csv)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="ascii") as fh:
        writer = csv.writer(fh)
        writer.writerow(["idx", "label", "pred", "cycles"])
        writer.writerows(rows)

    print("\n===== Batch Result =====", flush=True)
    print(f"Total:      {total}", flush=True)
    print(f"Correct:    {correct}", flush=True)
    print(f"Accuracy:   {100.0*correct/total:.2f}%", flush=True)
    print(f"Avg cycles: {avg_cyc:.0f}", flush=True)
    print(f"CSV:        {csv_path}", flush=True)
    errors = [(i, l, p) for i, l, p, _ in rows if l != p]
    for item in errors:
        print(f"MISCLASSIFIED idx={item[0]} label={item[1]} pred={item[2]}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
