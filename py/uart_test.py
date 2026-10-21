import serial
import time
import numpy as np
from torchvision import datasets, transforms

# ============ 配置 ============
SERIAL_PORT = 'COM9'      # 改成你的串口号
BAUD = 115200
N_TEST = 1000              # 测多少张

# ============ 加载 MNIST ============
transform = transforms.Compose([transforms.ToTensor()])
test_set = datasets.MNIST('./data', train=False, download=True, transform=transform)

# ============ 打开串口 ============
ser = serial.Serial(SERIAL_PORT, BAUD, timeout=10)
time.sleep(2)
ser.reset_input_buffer()

# ============ 等待 E203 就绪 ============
print("Waiting for E203...")
while True:
    line = ser.readline().decode('ascii', errors='ignore').strip()
    if line:
        print(line)
    if "CNN VERSION OK" in line:
        break

# ============ 批量测试 ============
results = []

for i in range(N_TEST):
    img = (test_set[i][0][0].numpy() * 255).astype(np.uint8)
    label = int(test_set[i][1])

    # 1. 发命令
    ser.write(f"RUN {label}\n".encode())
    ser.flush()

    # 2. 等 READY
    while True:
        line = ser.readline().decode('ascii', errors='ignore').strip()
        if line == "READY":
            break
        if line:
            print(f"  [debug] {line}")

    # 3. 发 784 字节
    ser.write(img.tobytes())
    ser.flush()

    # 4. 等 PRED
    while True:
        line = ser.readline().decode('ascii', errors='ignore').strip()
        if line.startswith("PRED"):
            parts = line.split()
            pred = int(parts[1])
            cycles = int(parts[3])
            results.append((i, label, pred, cycles))
            mark = "OK" if pred == label else "XX"
            print(f"[{i:3d}] label={label} pred={pred} cycles={cycles:>8} {mark}")
            break

# 5. 结束
ser.write(b"EXIT\n")
ser.close()

# ============ 统计 ============
correct = sum(1 for _, l, p, _ in results if l == p)
total = len(results)
avg_cyc = sum(c for *_, c in results) / total

print(f"\n===== Batch Result =====")
print(f"Total:      {total}")
print(f"Correct:    {correct}")
print(f"Accuracy:   {100.0 * correct / total:.2f}%")
print(f"Avg cycles: {avg_cyc:.0f}")

# 错误分布
errors = [(i, l, p) for i, l, p, _ in results if l != p]
if errors:
    print(f"\nMisclassified ({len(errors)}):")
    for i, l, p in errors:
        print(f"  idx={i}  label={l}  pred={p}")

# 存 CSV
with open('batch_result.csv', 'w') as f:
    f.write('idx,label,pred,cycles\n')
    for r in results:
        f.write(','.join(map(str, r)) + '\n')
print("\nSaved to batch_result.csv")