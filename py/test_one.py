"""
单张图对比测试：
  1. 从 MNIST test set 取一张图
  2. PC 端跑 infer_int8，打印逐层输出
  3. 通过串口把同一张图发给 E203
  4. 接收 E203 的逐层输出 + 预测结果
  5. 逐层对比，找第一个不一致的层
"""

import pickle
import time
import sys
import numpy as np
import serial
from torchvision import datasets, transforms

# ============ 配置 ============
SERIAL_PORT = 'COM9'      # 改成你的串口号
BAUD        = 115200
TEST_IDX    = 0           # 选 test set 的哪一张（0 ~ 9999）
QUANT_SHIFT = 8
OUT_NUM     = 10

# ============ 加载量化模型 ============
print("Loading qmodel.pkl ...")
with open('qmodel.pkl', 'rb') as f:
    qmodel = pickle.load(f)
print("OK")

# ============ 加载 MNIST test set ============
transform = transforms.Compose([transforms.ToTensor()])
test_set = datasets.MNIST('./data', train=False, download=True, transform=transform)

img = (test_set[TEST_IDX][0][0].numpy() * 255).astype(np.uint8)
label = int(test_set[TEST_IDX][1])
print(f"\nSelected: idx={TEST_IDX}, label={label}")
print(f"PY input[0:8]: {img.flatten()[:8].tolist()}")

# ============================================================
# PC 端软件参考推理
# ============================================================
def infer_int8(model_q, img_uint8, verbose=True):
    x = (img_uint8.astype(np.int32) * 100 // 255).astype(np.int32)

    # ---- C1 ----
    q = model_q['c1']
    w = q['w'].reshape(6, 5, 5).astype(np.int32)
    b = q['b'].astype(np.int32)
    out = np.zeros((6, 24, 24), dtype=np.int32)
    for oc in range(6):
        for oh in range(24):
            for ow in range(24):
                s = b[oc]
                for kh in range(5):
                    for kw in range(5):
                        s += x[oh+kh, ow+kw] * w[oc, kh, kw]
                out[oc, oh, ow] = min(max(s >> QUANT_SHIFT, 0), 127)
    x = out
    if verbose: print("PY C1[0:8]:", x.flatten()[:8].tolist())

    # ---- S2 ----
    x = np.maximum(
            np.maximum(x[:, 0::2, 0::2], x[:, 0::2, 1::2]),
            np.maximum(x[:, 1::2, 0::2], x[:, 1::2, 1::2]))
    if verbose: print("PY S2[0:8]:", x.flatten()[:8].tolist())

    # ---- C3 ----
    q = model_q['c3']
    w = q['w'].reshape(16, 6, 5, 5).astype(np.int32)
    b = q['b'].astype(np.int32)
    out = np.zeros((16, 8, 8), dtype=np.int32)
    for oc in range(16):
        for oh in range(8):
            for ow in range(8):
                s = b[oc]
                for ic in range(6):
                    for kh in range(5):
                        for kw in range(5):
                            s += x[ic, oh+kh, ow+kw] * w[oc, ic, kh, kw]
                out[oc, oh, ow] = min(max(s >> QUANT_SHIFT, 0), 127)
    x = out
    if verbose: print("PY C3[0:8]:", x.flatten()[:8].tolist())

    # ---- S4 ----
    x = np.maximum(
            np.maximum(x[:, 0::2, 0::2], x[:, 0::2, 1::2]),
            np.maximum(x[:, 1::2, 0::2], x[:, 1::2, 1::2]))
    if verbose: print("PY S4[0:8]:", x.flatten()[:8].tolist())

    # ---- C5 ----
    flat = x.flatten().astype(np.int32)
    q = model_q['c5']
    w = q['w'].reshape(120, 256).astype(np.int32)
    b = q['b'].astype(np.int32)
    y = np.zeros(120, dtype=np.int32)
    for i in range(120):
        y[i] = min(max((b[i] + np.dot(w[i], flat)) >> QUANT_SHIFT, 0), 127)
    if verbose: print("PY C5[0:8]:", y[:8].tolist())

    # ---- F6 ----
    q = model_q['f6']
    w = q['w'].reshape(84, 120).astype(np.int32)
    b = q['b'].astype(np.int32)
    z = np.zeros(84, dtype=np.int32)
    for i in range(84):
        z[i] = min(max((b[i] + np.dot(w[i], y)) >> QUANT_SHIFT, 0), 127)
    if verbose: print("PY F6[0:8]:", z[:8].tolist())

    # ---- OUT ----
    q = model_q['out']
    w = q['w'].reshape(10, 84).astype(np.int32)
    b = q['b'].astype(np.int32)
    out = np.zeros(10, dtype=np.int32)
    for i in range(10):
        out[i] = b[i] + np.dot(w[i], z)
    if verbose: print("PY OUT[0:10]:", out[:10].tolist())

    return out

# ============================================================
# PC 端推理
# ============================================================
print("\n===== PC (Python) Inference =====")
py_scores = infer_int8(qmodel, img, verbose=True)
py_pred = int(np.argmax(py_scores))
print(f"PY predicted: {py_pred}, label: {label}")

# ============================================================
# 串口通信
# ============================================================
print(f"\n===== Opening serial {SERIAL_PORT} =====", flush=True)
ser = serial.Serial(SERIAL_PORT, BAUD, timeout=2)
time.sleep(0.5)

# ---------- 等 E203 就绪（只等这一句）----------
print("Waiting for E203 ready...", flush=True)
t0 = time.time()
while time.time() - t0 < 30:
    line = ser.readline().decode('ascii', errors='ignore').strip()
    if line:
        print(f"  [E203] {line}", flush=True)
    if "Waiting for PC commands" in line:
        print("  E203 is ready.", flush=True)
        break
else:
    print("ERROR: E203 not ready in 30s", flush=True)
    ser.close()
    sys.exit(1)

# ---------- 发 RUN ----------
print(f"\nSending RUN {label}", flush=True)
ser.write(f"RUN {label}\n".encode())
ser.flush()

# ---------- 等 READY ----------
print("Waiting for READY...", flush=True)
t0 = time.time()
while time.time() - t0 < 5:
    line = ser.readline().decode('ascii', errors='ignore').strip()
    if line:
        print(f"  [E203] {line}", flush=True)
    if line == "READY":
        print("  Got READY.", flush=True)
        break
else:
    print("ERROR: no READY", flush=True)
    ser.close()
    sys.exit(1)

# ---------- 发 784 字节 ----------
print(f"Sending 784 bytes image...", flush=True)
ser.write(img.tobytes())
ser.flush()

# ---------- 接收结果 ----------
print("\n===== E203 (Hardware) Inference =====", flush=True)
hw_results = {}
hw_pred = None
hw_cycles = None
hw_input = None

t0 = time.time()
while time.time() - t0 < 30:
    line = ser.readline().decode('ascii', errors='ignore').strip()
    if not line:
        continue
    print(f"  {line}", flush=True)

    if line.startswith("HW input"):
        hw_input = [int(v) for v in line.rsplit(':', 1)[1].split()]
    elif line.startswith("HW C1"):
        hw_results['C1'] = [int(v) for v in line.rsplit(':', 1)[1].split()]
    elif line.startswith("HW S2"):
        hw_results['S2'] = [int(v) for v in line.rsplit(':', 1)[1].split()]
    elif line.startswith("HW C3"):
        hw_results['C3'] = [int(v) for v in line.rsplit(':', 1)[1].split()]
    elif line.startswith("HW S4"):
        hw_results['S4'] = [int(v) for v in line.rsplit(':', 1)[1].split()]
    elif line.startswith("HW C5"):
        hw_results['C5'] = [int(v) for v in line.rsplit(':', 1)[1].split()]
    elif line.startswith("HW F6"):
        hw_results['F6'] = [int(v) for v in line.rsplit(':', 1)[1].split()]
    elif line.startswith("HW OUT"):
        hw_results['OUT'] = [int(v) for v in line.rsplit(':', 1)[1].split()]
    elif line.startswith("PRED"):
        parts = line.split()
        hw_pred = int(parts[1])
        hw_cycles = int(parts[3])
        break

ser.close()

# ============================================================
# 对比
# ============================================================
print("\n===== Comparison =====")
print(f"PY pred: {py_pred}, HW pred: {hw_pred}, label: {label}")
print(f"HW cycles: {hw_cycles}")

# ★ 先看输入是否一致
print("\n===== Input Check =====")
py_input = img.flatten()[:8].tolist()
print(f"PY input[0:8]: {py_input}")
print(f"HW input[0:8]: {hw_input}")
if py_input == hw_input:
    print("  ✓ Input MATCH")
else:
    print("  ✗ Input MISMATCH —— E203 收到的图不对，先修 UART")

# 逐层对比
print(f"\n{'层':<6} {'PY[0:8]':<40} {'HW[0:8]':<40} {'一致?'}")
print('-' * 110)

# 重新跑一次 PC 侧，把每层数据保存下来
py_results = {}

x = (img.astype(np.int32) * 100 // 255).astype(np.int32)

q = qmodel['c1']
w = q['w'].reshape(6, 5, 5).astype(np.int32)
b = q['b'].astype(np.int32)
out = np.zeros((6, 24, 24), dtype=np.int32)
for oc in range(6):
    for oh in range(24):
        for ow in range(24):
            s = b[oc]
            for kh in range(5):
                for kw in range(5):
                    s += x[oh+kh, ow+kw] * w[oc, kh, kw]
            out[oc, oh, ow] = min(max(s >> QUANT_SHIFT, 0), 127)
py_results['C1'] = out.flatten()[:8].tolist()
x = out

x = np.maximum(
        np.maximum(x[:, 0::2, 0::2], x[:, 0::2, 1::2]),
        np.maximum(x[:, 1::2, 0::2], x[:, 1::2, 1::2]))
py_results['S2'] = x.flatten()[:8].tolist()

q = qmodel['c3']
w = q['w'].reshape(16, 6, 5, 5).astype(np.int32)
b = q['b'].astype(np.int32)
out = np.zeros((16, 8, 8), dtype=np.int32)
for oc in range(16):
    for oh in range(8):
        for ow in range(8):
            s = b[oc]
            for ic in range(6):
                for kh in range(5):
                    for kw in range(5):
                        s += x[ic, oh+kh, ow+kw] * w[oc, ic, kh, kw]
            out[oc, oh, ow] = min(max(s >> QUANT_SHIFT, 0), 127)
py_results['C3'] = out.flatten()[:8].tolist()
x = out

x = np.maximum(
        np.maximum(x[:, 0::2, 0::2], x[:, 0::2, 1::2]),
        np.maximum(x[:, 1::2, 0::2], x[:, 1::2, 1::2]))
py_results['S4'] = x.flatten()[:8].tolist()

flat = x.flatten().astype(np.int32)
q = qmodel['c5']
w = q['w'].reshape(120, 256).astype(np.int32)
b = q['b'].astype(np.int32)
y = np.zeros(120, dtype=np.int32)
for i in range(120):
    y[i] = min(max((b[i] + np.dot(w[i], flat)) >> QUANT_SHIFT, 0), 127)
py_results['C5'] = y[:8].tolist()

q = qmodel['f6']
w = q['w'].reshape(84, 120).astype(np.int32)
b = q['b'].astype(np.int32)
z = np.zeros(84, dtype=np.int32)
for i in range(84):
    z[i] = min(max((b[i] + np.dot(w[i], y)) >> QUANT_SHIFT, 0), 127)
py_results['F6'] = z[:8].tolist()

q = qmodel['out']
w = q['w'].reshape(10, 84).astype(np.int32)
b = q['b'].astype(np.int32)
out = np.zeros(10, dtype=np.int32)
for i in range(10):
    out[i] = b[i] + np.dot(w[i], z)
py_results['OUT'] = out[:10].tolist()

for layer in ['C1', 'S2', 'C3', 'S4', 'C5', 'F6', 'OUT']:
    py_vals = py_results.get(layer, [])
    hw_vals = hw_results.get(layer, [])
    match = (py_vals == hw_vals)
    py_str = str(py_vals)
    hw_str = str(hw_vals)
    mark = "✓" if match else "✗ MISMATCH"
    print(f"{layer:<6} {py_str:<40} {hw_str:<40} {mark}")

print("\n===== Done =====")