from __future__ import annotations

import argparse
import csv
import gzip
import json
import os
import re
import struct
import subprocess
import threading
import time
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import numpy as np
import serial


APP_DIR = Path(__file__).resolve().parent
REPO_ROOT = APP_DIR.parents[1]
WORK_DIR = APP_DIR / ".work"
DATASET_DIR = REPO_ROOT / "dataset" / "mnist"
RESULTS_DIR = APP_DIR / "results"
BATCH_ELF = REPO_ROOT / "sdk" / "LeNet_hw" / "Debug" / "LeNet_hw.elf"
SELFTEST_ELF = REPO_ROOT / "LeNet_oneP" / "Debug" / "LeNet_oneP.elf"
OPENOCD_CFG = APP_DIR / "openocd" / "e203_pynq.cfg"
LOAD_TCL = APP_DIR / "openocd" / "run_e203_bin.tcl"
OBJCOPY = Path(os.environ.get("NUCLEI_OBJCOPY", "riscv-nuclei-elf-objcopy"))
OPENOCD = Path(os.environ.get("OPENOCD_EXE", "openocd"))
DEFAULT_PORT = "COM9"
DEFAULT_BAUD = 57600
SELFTEST_BAUD = 115200
DEFAULT_CPU_HZ = 16_000_000


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_idx(path: Path) -> np.ndarray:
    with gzip.open(path, "rb") as fh:
        magic, count = struct.unpack(">II", fh.read(8))
        if magic == 2051:
            rows, cols = struct.unpack(">II", fh.read(8))
            return np.frombuffer(fh.read(count * rows * cols), dtype=np.uint8).reshape(count, rows, cols)
        if magic == 2049:
            return np.frombuffer(fh.read(count), dtype=np.uint8)
        raise ValueError(f"unexpected IDX magic {magic:#x} in {path}")


class BoardBridge:
    """Single-owner serial bridge to the E203 batch firmware."""

    def __init__(self, port: str, baud: int):
        self.port = port
        self.baud = baud
        self.ser: serial.Serial | None = None
        self.online = False
        self.last_seen = 0.0
        self.last_error = ""
        self.lock = threading.RLock()

    def close(self) -> None:
        with self.lock:
            if self.ser is not None:
                try:
                    self.ser.close()
                except Exception:
                    pass
            self.ser = None
            self.online = False

    def connect(self) -> tuple[bool, str]:
        with self.lock:
            self.close()
            try:
                ser = serial.Serial(self.port, self.baud, timeout=0.35)
                time.sleep(0.18)
                ser.reset_input_buffer()
                for _ in range(4):
                    ser.write(b"\nBEG 0\n")
                    ser.flush()
                    deadline = time.time() + 1.5
                    while time.time() < deadline:
                        line = ser.readline().decode("ascii", errors="replace").strip()
                        if not line:
                            continue
                        if line.startswith("READY"):
                            self.ser = ser
                            self.online = True
                            self.last_seen = time.time()
                            self.last_error = ""
                            return True, "firmware handshake OK"
                    time.sleep(0.08)
                ser.close()
                raise RuntimeError("no READY response from E203 batch firmware")
            except Exception as exc:
                self.ser = None
                self.online = False
                self.last_error = str(exc)
                return False, str(exc)

    def _wait_prefix(self, prefixes: tuple[str, ...], timeout: float) -> str:
        if self.ser is None:
            raise RuntimeError("serial port is not open")
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = self.ser.readline().decode("ascii", errors="replace").strip()
            if not line:
                continue
            if any(line.startswith(prefix) for prefix in prefixes):
                self.last_seen = time.time()
                return line
        raise TimeoutError(f"timeout waiting for {prefixes}")

    def _command_retry(self, command: bytes, prefixes: tuple[str, ...], timeout: float = 2.0, retries: int = 8) -> str:
        last_exc: Exception | None = None
        for _ in range(retries):
            try:
                if self.ser is None:
                    raise RuntimeError("serial port is not open")
                self.ser.write(command)
                self.ser.flush()
                return self._wait_prefix(prefixes, timeout)
            except Exception as exc:
                last_exc = exc
                try:
                    if self.ser is not None:
                        self.ser.write(b"\n")
                        self.ser.flush()
                        time.sleep(0.05)
                        self.ser.reset_input_buffer()
                except Exception:
                    pass
        raise RuntimeError(f"command failed after {retries} attempts: {last_exc}")

    def run_image(self, image: np.ndarray, index: int, label: int) -> dict:
        with self.lock:
            if not self.online:
                ok, msg = self.connect()
                if not ok:
                    raise RuntimeError(msg)
            try:
                # Keep each UART line below the E203 receive buffer pressure point.
                # A truncated DATA line produces a short ACK; resend that same chunk.
                self._command_retry(f"BEG {int(label)}\n".encode("ascii"), ("READY",))
                payload = np.asarray(image, dtype=np.uint8).reshape(-1).tobytes()
                chunk_size = 64
                for off in range(0, len(payload), chunk_size):
                    chunk = payload[off:off + chunk_size]
                    expected = f"ACK {off} {len(chunk)}"
                    command = f"DATA {off} {chunk.hex()}\n".encode("ascii")
                    line = ""
                    for _ in range(10):
                        try:
                            line = self._command_retry(command, ("ACK ",), timeout=2.0, retries=2)
                        except Exception:
                            line = ""
                        if line == expected:
                            break
                        time.sleep(0.025)
                        try:
                            if self.ser is not None:
                                self.ser.reset_input_buffer()
                        except Exception:
                            pass
                    if line != expected:
                        raise RuntimeError(f"bad ACK after retries: got {line!r}, expected {expected!r}")
                    time.sleep(0.004)
                line = self._command_retry(b"END\n", ("PRED ",), timeout=4.0, retries=6)
                parts = line.split()
                if len(parts) < 4:
                    raise RuntimeError(f"malformed PRED line: {line!r}")
                pred = int(parts[1])
                returned_label = int(parts[2])
                cycles = int(parts[3])
                self.online = True
                return {
                    "index": int(index),
                    "label": int(label),
                    "pred": pred,
                    "returned_label": returned_label,
                    "cycles": cycles,
                    "match": pred == int(label),
                    "raw": line,
                }
            except Exception as exc:
                self.last_error = str(exc)
                self.online = False
                self.close()
                raise


class JobManager:
    def __init__(self, bridge: BoardBridge, images: np.ndarray, labels: np.ndarray, cpu_hz: int):
        self.bridge = bridge
        self.images = images
        self.labels = labels
        self.cpu_hz = cpu_hz
        self.cond = threading.Condition()
        self.cancel_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.job: dict = {
            "id": "",
            "kind": "idle",
            "status": "idle",
            "message": "等待实板指令",
            "started_at": "",
            "finished_at": "",
            "total": 0,
            "completed": 0,
            "correct": 0,
            "accuracy": 0.0,
            "avg_cycles": 0,
            "current_index": None,
            "current_label": None,
            "current_pred": None,
            "current_cycles": None,
            "preview": None,
            "logs": [],
            "recent": [],
            "errors": [],
            "metrics": {},
            "csv_path": "",
        }
        self.selftest_metrics: dict = {}

    def snapshot(self) -> dict:
        with self.cond:
            job = json.loads(json.dumps(self.job))
            job["logs"] = job.get("logs", [])[-80:]
            job["cpu_hz"] = self.cpu_hz
            return job

    def is_running(self) -> bool:
        with self.cond:
            return self.job.get("status") in {"starting", "running", "restoring"}

    def _new_job(self, kind: str, message: str, total: int = 0) -> dict:
        job = {
            "id": datetime.now().strftime("%Y%m%d_%H%M%S_%f"),
            "kind": kind,
            "status": "starting",
            "message": message,
            "started_at": now_iso(),
            "finished_at": "",
            "total": int(total),
            "completed": 0,
            "correct": 0,
            "accuracy": 0.0,
            "avg_cycles": 0,
            "current_index": None,
            "current_label": None,
            "current_pred": None,
            "current_cycles": None,
            "preview": None,
            "logs": [],
            "recent": [],
            "errors": [],
            "metrics": {},
            "csv_path": "",
        }
        return job

    def _set(self, **kwargs) -> None:
        with self.cond:
            self.job.update(kwargs)
            self.cond.notify_all()

    def _log(self, text: str) -> None:
        with self.cond:
            logs = self.job.setdefault("logs", [])
            logs.append({"t": datetime.now().strftime("%H:%M:%S"), "text": text})
            if len(logs) > 240:
                del logs[:80]
            self.cond.notify_all()

    def start_single(self, index: int) -> tuple[bool, str]:
        with self.cond:
            if self.job.get("status") in {"starting", "running", "restoring"}:
                return False, "已有任务正在运行"
            self.cancel_event.clear()
            self.job = self._new_job("single", "准备发送单张图像", total=1)
        self.thread = threading.Thread(target=self._run_single, args=(int(index),), daemon=True)
        self.thread.start()
        return True, self.job["id"]

    def _run_single(self, index: int) -> None:
        try:
            index = max(0, min(index, len(self.images) - 1))
            image = self.images[index]
            label = int(self.labels[index])
            self._set(status="running", message="UART 发送图像并等待 CNN 完成", current_index=index,
                      current_label=label, preview=np.asarray(image).reshape(-1).astype(int).tolist())
            self._log(f"载入 MNIST 样本 #{index}，标签 {label}")
            self._log("BEG → DATA ×7 → END")
            result = self.bridge.run_image(image, index, label)
            elapsed_ms = result["cycles"] * 1000.0 / self.cpu_hz
            self._set(
                status="done",
                message="单张实板推理完成",
                finished_at=now_iso(),
                completed=1,
                correct=1 if result["match"] else 0,
                accuracy=100.0 if result["match"] else 0.0,
                avg_cycles=result["cycles"],
                current_pred=result["pred"],
                current_cycles=result["cycles"],
                recent=[result],
                metrics={
                    "elapsed_ms": round(elapsed_ms, 3),
                    "cpu_hz": self.cpu_hz,
                    "returned_label": result["returned_label"],
                },
            )
            self._log(f"E203 返回 {result['raw']}；预计 {elapsed_ms:.2f} ms @ {self.cpu_hz/1e6:.3f} MHz")
        except Exception as exc:
            self._set(status="error", message=f"推理失败：{exc}", finished_at=now_iso())
            self._log(f"ERROR: {exc}")

    def start_batch(self, start: int, count: int) -> tuple[bool, str]:
        with self.cond:
            if self.job.get("status") in {"starting", "running", "restoring"}:
                return False, "已有任务正在运行"
            start = max(0, min(int(start), len(self.images) - 1))
            count = max(1, min(int(count), len(self.images) - start))
            self.cancel_event.clear()
            self.job = self._new_job("batch", f"准备评测 {count} 张 MNIST", total=count)
            self.job["metrics"]["start_index"] = start
        self.thread = threading.Thread(target=self._run_batch, args=(start, count), daemon=True)
        self.thread.start()
        return True, self.job["id"]

    def _infer_with_retry(self, image: np.ndarray, index: int, label: int) -> dict:
        last_exc: Exception | None = None
        for attempt in range(2):
            try:
                return self.bridge.run_image(image, index, label)
            except Exception as exc:
                last_exc = exc
                if attempt == 0:
                    self._log(f"样本 #{index} 串口传输异常，自动重连后重试整张图")
                    time.sleep(0.12)
                    self.bridge.connect()
        raise RuntimeError(str(last_exc))

    def _run_batch(self, start: int, count: int) -> None:
        csv_path = RESULTS_DIR / f"batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        recent: deque[dict] = deque(maxlen=16)
        errors: deque[dict] = deque(maxlen=30)
        correct = 0
        cycles_sum = 0
        completed = 0
        try:
            self._set(status="running", message="实板批测运行中", csv_path=str(csv_path))
            with csv_path.open("w", newline="", encoding="ascii") as fh:
                writer = csv.writer(fh)
                writer.writerow(["idx", "label", "pred", "cycles"])
                for offset in range(count):
                    if self.cancel_event.is_set():
                        break
                    index = start + offset
                    image = self.images[index]
                    label = int(self.labels[index])
                    self._set(current_index=index, current_label=label, preview=np.asarray(image).reshape(-1).astype(int).tolist())
                    result = self._infer_with_retry(image, index, label)
                    completed += 1
                    correct += 1 if result["match"] else 0
                    cycles_sum += result["cycles"]
                    writer.writerow([index, label, result["pred"], result["cycles"]])
                    fh.flush()
                    recent.appendleft(result)
                    if not result["match"]:
                        errors.appendleft(result)
                    self._set(
                        completed=completed,
                        correct=correct,
                        accuracy=round(100.0 * correct / completed, 2),
                        avg_cycles=round(cycles_sum / completed),
                        current_pred=result["pred"],
                        current_cycles=result["cycles"],
                        recent=list(recent),
                        errors=list(errors),
                        message=f"{completed}/{count} 张已由实板完成",
                    )
                    if completed == 1 or completed % 10 == 0 or completed == count:
                        self._log(f"[{completed:4d}/{count}] idx={index} label={label} pred={result['pred']} cycles={result['cycles']}")
                    time.sleep(0.05)
        except Exception as exc:
            self._set(status="error", message=f"批测中断：{exc}", finished_at=now_iso(), csv_path=str(csv_path))
            self._log(f"ERROR: {exc}")
            return

        cancelled = self.cancel_event.is_set()
        final_status = "cancelled" if cancelled else "done"
        final_word = "已停止" if cancelled else "批测完成"
        self._set(
            status=final_status,
            message=f"{final_word}，共 {completed} 张",
            finished_at=now_iso(),
            completed=completed,
            correct=correct,
            accuracy=round(100.0 * correct / completed, 2) if completed else 0.0,
            avg_cycles=round(cycles_sum / completed) if completed else 0,
            csv_path=str(csv_path),
        )
        self._log(f"{final_word}：{correct}/{completed}，准确率 {100.0*correct/completed:.2f}%" if completed else final_word)

    def stop(self) -> None:
        self.cancel_event.set()
        self._log("已请求停止；当前单张命令完成后停止")

    def reset_demo(self) -> tuple[bool, str]:
        """Clear all presentation state while keeping the board firmware running."""
        with self.cond:
            if self.job.get("status") in {"starting", "running", "restoring"}:
                return False, "任务正在运行，请先停止并等待当前命令结束"
            self.cancel_event.clear()
            self.job = self._new_job("idle", "等待实板指令", total=0)
            self.job.update(status="idle", id="", started_at="", finished_at="")
            self.selftest_metrics = {}
            self.cond.notify_all()
        return True, "演示界面已归零"

    def start_selftest(self) -> tuple[bool, str]:
        with self.cond:
            if self.job.get("status") in {"starting", "running", "restoring"}:
                return False, "已有任务正在运行"
            self.cancel_event.clear()
            self.job = self._new_job("selftest", "切换自检固件并运行 Pool / Sobel", total=1)
        self.thread = threading.Thread(target=self._run_selftest, daemon=True)
        self.thread.start()
        return True, self.job["id"]

    @staticmethod
    def _popen_loader(elf_path: Path) -> subprocess.Popen:
        """Convert an ELF to raw BIN and load it through the bundled OpenOCD TCL."""
        if not elf_path.exists():
            raise FileNotFoundError(f"firmware ELF not found: {elf_path}")
        if not OPENOCD_CFG.exists():
            raise FileNotFoundError(f"OpenOCD config not found: {OPENOCD_CFG}")
        if not LOAD_TCL.exists():
            raise FileNotFoundError(f"OpenOCD load TCL not found: {LOAD_TCL}")
        WORK_DIR.mkdir(parents=True, exist_ok=True)
        bin_path = WORK_DIR / f"{elf_path.stem}.bin"
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        objcopy_run = subprocess.run(
            [str(OBJCOPY), "-O", "binary", str(elf_path), str(bin_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=flags,
        )
        if objcopy_run.returncode != 0:
            raise RuntimeError(f"objcopy failed: {objcopy_run.stdout[-500:]}")
        cmd = [
            str(OPENOCD),
            "-f", str(OPENOCD_CFG),
            "-c", f"set IMAGE {{{bin_path.as_posix()}}}",
            "-c", "set ENTRY 0x80000000",
            "-c", f"source {{{LOAD_TCL.as_posix()}}}",
        ]
        return subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="gbk",
            errors="replace",
            creationflags=flags,
        )

    def _run_selftest(self) -> None:
        capture: list[str] = []
        proc: subprocess.Popen | None = None
        ser: serial.Serial | None = None
        metrics: dict = {}
        try:
            self._set(status="running", message="正在加载 LeNet_oneP 自检固件")
            self.bridge.close()
            self._log("关闭批处理串口，准备切换自检固件")
            ser = serial.Serial(self.bridge.port, SELFTEST_BAUD, timeout=0.45)
            ser.reset_input_buffer()
            proc = self._popen_loader(SELFTEST_ELF)
            deadline = time.time() + 22
            quiet_since = time.time()
            while time.time() < deadline:
                line = ser.readline().decode("ascii", errors="replace").strip()
                if line:
                    capture.append(line)
                    quiet_since = time.time()
                    self._log(line)
                    if "Predicted class:" in line:
                        metrics["predicted_class"] = int(line.split(":", 1)[1].strip())
                    if line.startswith("POOL SELFTEST:"):
                        metrics["pool"] = line.split(":", 1)[1].strip()
                    if line.startswith("SOBEL SELFTEST:"):
                        metrics["sobel"] = line.split(":", 1)[1].strip()
                    if "PMU MAC=" in line:
                        for key, value in re.findall(r"(MAC|WLOAD|XLOAD|BUBBLE|TOTAL|AXIWR)=(\d+)", line):
                            metrics[key.lower()] = int(value)
                    if line.startswith("Total cycles:"):
                        metrics["cycles"] = int(line.split(":", 1)[1].strip())
                    if line.startswith("CPU Frequency:"):
                        metrics["cpu_hz"] = int(line.split(":", 1)[1].replace("Hz", "").strip())
                    if line.startswith("SOBEL SELFTEST:"):
                        break
                elif proc.poll() is not None and time.time() - quiet_since > 1.8:
                    break
            if proc.poll() is None:
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
            if not capture:
                raise RuntimeError("自检固件没有产生串口输出")
            if "cpu_hz" in metrics:
                self.cpu_hz = metrics["cpu_hz"]
            self.selftest_metrics = dict(metrics)
            self._set(
                status="restoring",
                message="自检完成，正在恢复实板交互固件",
                metrics=metrics,
                finished_at=now_iso(),
            )
        except Exception as exc:
            self._set(status="error", message=f"自检失败：{exc}", finished_at=now_iso())
            self._log(f"ERROR: {exc}")
        finally:
            if ser is not None:
                try:
                    ser.close()
                except Exception:
                    pass
            if proc is not None and proc.poll() is None:
                try:
                    proc.kill()
                except Exception:
                    pass

        if any(line.startswith("SOBEL SELFTEST:") for line in capture):
            try:
                self._set(status="restoring", message="恢复 LeNet_hw 批处理固件")
                restore = self._popen_loader(BATCH_ELF)
                restore_stdout, _ = restore.communicate(timeout=25)
                if restore.returncode != 0:
                    raise RuntimeError(f"恢复固件加载失败：{restore_stdout[-400:]}")
                ok, msg = self.bridge.connect()
                if not ok:
                    raise RuntimeError(f"固件已恢复但串口握手失败：{msg}")
                self._set(status="done", message="Pool / Sobel 自检完成，已恢复实板交互", finished_at=now_iso())
                self._log("已恢复 LeNet_hw；在线推理功能可用")
            except Exception as exc:
                self._set(status="error", message=f"自检后恢复失败：{exc}", finished_at=now_iso())
                self._log(f"ERROR: {exc}")


class ApiServer:
    def __init__(self, host: str, port: int, manager: JobManager, bridge: BoardBridge):
        self.host = host
        self.port = port
        self.manager = manager
        self.bridge = bridge
        self.httpd: ThreadingHTTPServer | None = None

    def status(self) -> dict:
        return {
            "board": {
                "port": self.bridge.port,
                "baud": self.bridge.baud,
                "online": self.bridge.online,
                "last_seen": self.bridge.last_seen,
                "last_error": self.bridge.last_error,
            },
            "dataset_count": int(len(self.manager.images)),
            "job": self.manager.snapshot(),
            "server_time": now_iso(),
        }

    def make_handler(self):
        app = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "E203RealDemo/1.0"

            def log_message(self, fmt, *args):
                return

            def send_json(self, payload, status=200):
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def read_json(self):
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0:
                    return {}
                return json.loads(self.rfile.read(length).decode("utf-8"))

            def do_GET(self):
                parsed = urlparse(self.path)
                if parsed.path == "/api/status":
                    self.send_json(app.status())
                    return
                if parsed.path == "/api/image":
                    query = parse_qs(parsed.query)
                    index = int(query.get("index", ["0"])[0])
                    index = max(0, min(index, len(app.manager.images) - 1))
                    image = app.manager.images[index]
                    self.send_json({
                        "index": index,
                        "label": int(app.manager.labels[index]),
                        "pixels": np.asarray(image).reshape(-1).astype(int).tolist(),
                    })
                    return
                if parsed.path in {"/", "/index.html"}:
                    body = (APP_DIR / "index.html").read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_error(404)

            def do_POST(self):
                parsed = urlparse(self.path)
                try:
                    data = self.read_json()
                    if parsed.path == "/api/infer":
                        ok, msg = app.manager.start_single(int(data.get("index", 0)))
                        self.send_json({"ok": ok, "message": msg}, 200 if ok else 409)
                        return
                    if parsed.path == "/api/batch":
                        ok, msg = app.manager.start_batch(int(data.get("start", 0)), int(data.get("count", 1000)))
                        self.send_json({"ok": ok, "message": msg}, 200 if ok else 409)
                        return
                    if parsed.path == "/api/selftest":
                        ok, msg = app.manager.start_selftest()
                        self.send_json({"ok": ok, "message": msg}, 200 if ok else 409)
                        return
                    if parsed.path == "/api/reset":
                        ok, msg = app.manager.reset_demo()
                        self.send_json({"ok": ok, "message": msg}, 200 if ok else 409)
                        return
                    if parsed.path == "/api/stop":
                        app.manager.stop()
                        self.send_json({"ok": True})
                        return
                    if parsed.path == "/api/reconnect":
                        ok, msg = app.bridge.connect()
                        self.send_json({"ok": ok, "message": msg}, 200 if ok else 503)
                        return
                    self.send_error(404)
                except Exception as exc:
                    self.send_json({"ok": False, "message": str(exc)}, 500)

        return Handler

    def run(self):
        self.httpd = ThreadingHTTPServer((self.host, self.port), self.make_handler())
        print(f"E203 live demo: http://{self.host}:{self.port}", flush=True)
        print(f"Serial: {self.bridge.port} @ {self.bridge.baud}", flush=True)
        self.httpd.serve_forever()


def main():
    global DATASET_DIR, RESULTS_DIR, BATCH_ELF, SELFTEST_ELF
    global OBJCOPY, OPENOCD, OPENOCD_CFG, LOAD_TCL
    parser = argparse.ArgumentParser(description="E203 INT8 CNN real-board demo dashboard")
    parser.add_argument("--port", default=DEFAULT_PORT, help="serial port, for example COM9")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    parser.add_argument("--http-port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path, default=DATASET_DIR, help="directory containing MNIST t10k gzip files")
    parser.add_argument("--results-dir", type=Path, default=RESULTS_DIR)
    parser.add_argument("--batch-elf", type=Path, default=BATCH_ELF)
    parser.add_argument("--selftest-elf", type=Path, default=SELFTEST_ELF)
    parser.add_argument("--objcopy", type=Path, default=OBJCOPY, help="Nuclei riscv objcopy executable")
    parser.add_argument("--openocd", type=Path, default=OPENOCD, help="OpenOCD executable")
    parser.add_argument("--openocd-cfg", type=Path, default=OPENOCD_CFG)
    parser.add_argument("--load-tcl", type=Path, default=LOAD_TCL)
    args = parser.parse_args()

    DATASET_DIR = args.data_dir.resolve()
    RESULTS_DIR = args.results_dir.resolve()
    BATCH_ELF = args.batch_elf.resolve()
    SELFTEST_ELF = args.selftest_elf.resolve()
    OBJCOPY = args.objcopy
    OPENOCD = args.openocd
    OPENOCD_CFG = args.openocd_cfg.resolve()
    LOAD_TCL = args.load_tcl.resolve()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    image_path = DATASET_DIR / "t10k-images-idx3-ubyte.gz"
    label_path = DATASET_DIR / "t10k-labels-idx1-ubyte.gz"
    if not image_path.exists() or not label_path.exists():
        parser.error(f"MNIST t10k files not found under {DATASET_DIR}")
    images = load_idx(image_path)
    labels = load_idx(label_path)
    bridge = BoardBridge(args.port, args.baud)
    ok, msg = bridge.connect()
    manager = JobManager(bridge, images, labels, DEFAULT_CPU_HZ)
    manager._log("服务启动：" + ("实板握手成功" if ok else f"实板握手失败：{msg}"))
    server = ApiServer("127.0.0.1", args.http_port, manager, bridge)
    server.run()


if __name__ == "__main__":
    main()
