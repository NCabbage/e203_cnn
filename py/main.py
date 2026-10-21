"""
LeNet-5 训练 + INT8 量化权重导出（含硬件布局 + 32bit ROM 打包）
Pooling: 2x2 MAX（与硬件池化语义一致）

输出：
  lenet_weights.h       软件布局（PyTorch 顺序）
  lenet_weights_hw.h    硬件布局（[KH][KW][C][OC]）
  lenet_c5_split.h      C5 拆分（原样保留）
  rom_all.hex / .coe    OC 顺序打包
  rom_all_tap.hex/.coe  TAP 顺序打包
  wbase.h / wbase_tap.h 每层字地址
  qmodel.pkl            量化后的模型（供 test_one.py 使用）
"""

import torch
import torch.nn as nn
import torch.nn.functional as F
from torchvision import datasets, transforms
from torch.utils.data import DataLoader
import numpy as np
import pickle

# ============================================================
# 0. 量化参数
# ============================================================
QUANT_SHIFT = 12
S_IN        = 1.0 / 100.0

# ============================================================
# 0.5 调试开关
# ============================================================
DEBUG_LAYER = True   # True = 打印每层前 8 个值，用于和硬件对比

# ============================================================
# 1. 网络结构（max_pool2d）
# ============================================================
class LeNet5(nn.Module):
    def __init__(self):
        super().__init__()
        self.c1  = nn.Conv2d(1, 6, 5)
        self.c3  = nn.Conv2d(6, 16, 5)
        self.c5  = nn.Linear(16 * 4 * 4, 120)
        self.f6  = nn.Linear(120, 84)
        self.out = nn.Linear(84, 10)

    def forward(self, x):
        x = F.relu(self.c1(x))
        x = F.max_pool2d(x, 2)
        x = F.relu(self.c3(x))
        x = F.max_pool2d(x, 2)
        x = torch.flatten(x, 1)
        x = F.relu(self.c5(x))
        x = F.relu(self.f6(x))
        x = self.out(x)
        return x

# ============================================================
# 2. 训练 / 评估
# ============================================================
def train_one_epoch(model, device, loader, optimizer):
    model.train()
    for data, target in loader:
        data, target = data.to(device), target.to(device)
        optimizer.zero_grad()
        loss = F.cross_entropy(model(data), target)
        loss.backward()
        optimizer.step()

def evaluate(model, device, loader):
    model.eval()
    correct = 0
    with torch.no_grad():
        for data, target in loader:
            data, target = data.to(device), target.to(device)
            pred = model(data).argmax(dim=1)
            correct += pred.eq(target).sum().item()
    return 100.0 * correct / len(loader.dataset)

# ============================================================
# 3. 量化工具
# ============================================================
def quantize_weight(w: torch.Tensor):
    max_abs = w.abs().max().item()
    if max_abs < 1e-12:
        return torch.zeros_like(w, dtype=torch.int8), 1.0
    s_w = max_abs / 127.0
    q = torch.round(w / s_w).clamp(-128, 127).to(torch.int8)
    return q, s_w

def quantize_model(model: LeNet5, s_in: float):
    result = {}
    s_x = s_in

    layers = [
        ('c1',  model.c1.weight.data,  model.c1.bias.data),
        ('c3',  model.c3.weight.data,  model.c3.bias.data),
        ('c5',  model.c5.weight.data,  model.c5.bias.data),
        ('f6',  model.f6.weight.data,  model.f6.bias.data),
        ('out', model.out.weight.data, model.out.bias.data),
    ]

    for name, w, b in layers:
        q_w, s_w = quantize_weight(w)
        b_int = torch.round(b / (s_x * s_w)).to(torch.int32)
        s_y = s_x * s_w * (2 ** QUANT_SHIFT)

        result[name] = {
            'w':   q_w.cpu().numpy().astype(np.int8),
            'b':   b_int.cpu().numpy().astype(np.int32),
            's_w': s_w,
            's_in': s_x,
            's_out': s_y,
        }
        print(f'  {name:>3s}: s_in={s_x:.6e}  s_w={s_w:.6e}  s_out={s_y:.6e}  '
              f'b_range=[{b_int.min().item()}, {b_int.max().item()}]')

        s_x = s_y

    return result

# ============================================================
# 4. 转置到硬件布局
# ============================================================
def transpose_to_hw(qmodel):
    hw = {}

    w = qmodel['c1']['w'].reshape(6, 1, 5, 5)
    w_hw = w.transpose(2, 3, 1, 0)
    hw['c1'] = {'w': w_hw.flatten(), 'b': qmodel['c1']['b']}
    print(f'  c1  软件 {w.shape} -> 硬件 {w_hw.shape}  ({w_hw.size} 个)')

    w = qmodel['c3']['w'].reshape(16, 6, 5, 5)
    w_hw = w.transpose(2, 3, 1, 0)
    hw['c3'] = {'w': w_hw.flatten(), 'b': qmodel['c3']['b']}
    print(f'  c3  软件 {w.shape} -> 硬件 {w_hw.shape}  ({w_hw.size} 个)')

    w = qmodel['c5']['w'].reshape(120, 256)
    w_hw = np.zeros((4, 4, 16, 120), dtype=np.int8)
    for oc in range(120):
        for c in range(16):
            for kh in range(4):
                for kw in range(4):
                    w_hw[kh, kw, c, oc] = w[oc, c*16 + kh*4 + kw]
    hw['c5'] = {'w': w_hw.flatten(), 'b': qmodel['c5']['b']}
    print(f'  c5  软件 {w.shape} -> 硬件 {w_hw.shape}  ({w_hw.size} 个)')

    w = qmodel['f6']['w'].reshape(84, 120)
    w_hw = w.transpose(1, 0)
    hw['f6'] = {'w': w_hw.flatten(), 'b': qmodel['f6']['b']}
    print(f'  f6  软件 {w.shape} -> 硬件 {w_hw.shape}  ({w_hw.size} 个)')

    w = qmodel['out']['w'].reshape(10, 84)
    w_hw = w.transpose(1, 0)
    hw['out'] = {'w': w_hw.flatten(), 'b': qmodel['out']['b']}
    print(f'  out 软件 {w.shape} -> 硬件 {w_hw.shape}  ({w_hw.size} 个)')

    return hw

# ============================================================
# 5. 导出 C 头文件
# ============================================================
def _fmt_i8(arr, per_line=16):
    lines = []
    flat = arr.flatten()
    for i in range(0, len(flat), per_line):
        chunk = flat[i:i + per_line]
        lines.append('    ' + ', '.join(f'{int(v):4d}' for v in chunk) + ',')
    return '\n'.join(lines)

def _fmt_i32(arr, per_line=8):
    lines = []
    flat = arr.flatten()
    for i in range(0, len(flat), per_line):
        chunk = flat[i:i + per_line]
        lines.append('    ' + ', '.join(f'{int(v):8d}' for v in chunk) + ',')
    return '\n'.join(lines)

def export_c_header_sw(qmodel, filename='lenet_weights.h'):
    with open(filename, 'w') as f:
        f.write('// Auto-generated by lenet_export.py (SOFTWARE LAYOUT)\n')
        f.write('// Layout: PyTorch standard ([OC][IC][KH][KW] for conv)\n')
        f.write('// Quantization: int8 weights, int32 bias, QUANT_SHIFT = %d\n' % QUANT_SHIFT)
        f.write('#ifndef LENET_WEIGHTS_H\n#define LENET_WEIGHTS_H\n\n')
        f.write('#include <stdint.h>\n\n')

        for name in ['c1', 'c3', 'c5', 'f6', 'out']:
            w = qmodel[name]['w'].flatten()
            b = qmodel[name]['b'].flatten()
            f.write(f'// ---- {name.upper()} ----\n')
            f.write(f'static const int8_t {name}_weight[{w.size}] = {{\n')
            f.write(_fmt_i8(w) + '\n};\n')
            f.write(f'static const int32_t {name}_bias[{b.size}] = {{\n')
            f.write(_fmt_i32(b) + '\n};\n\n')

        f.write('#endif // LENET_WEIGHTS_H\n')
    print(f'[OK] 软件布局导出到 {filename}')

def export_c_header_hw(hw, filename='lenet_weights_hw.h'):
    with open(filename, 'w') as f:
        f.write('// Auto-generated by lenet_export.py (HARDWARE LAYOUT)\n')
        f.write('// Layout: [KH][KW][C][OC] for all layers\n')
        f.write('//   C1:  K=5, C=1,   OC=6\n')
        f.write('//   C3:  K=5, C=6,   OC=16\n')
        f.write('//   C5:  K=4, C=16,  OC=120\n')
        f.write('//   F6:  K=1, C=120, OC=84\n')
        f.write('//   OUT: K=1, C=84,  OC=10\n')
        f.write('// Quantization: int8 weights, int32 bias, QUANT_SHIFT = %d\n' % QUANT_SHIFT)
        f.write('#ifndef LENET_WEIGHTS_HW_H\n#define LENET_WEIGHTS_HW_H\n\n')
        f.write('#include <stdint.h>\n\n')

        for name in ['c1', 'c3', 'c5', 'f6', 'out']:
            w = hw[name]['w']
            b = hw[name]['b']
            f.write(f'// ---- {name.upper()} ----\n')
            f.write(f'static const int8_t {name}_weight_hw[{w.size}] = {{\n')
            f.write(_fmt_i8(w) + '\n};\n')
            f.write(f'static const int32_t {name}_bias[{b.size}] = {{\n')
            f.write(_fmt_i32(b) + '\n};\n\n')

        f.write('#endif // LENET_WEIGHTS_HW_H\n')
    print(f'[OK] 硬件布局导出到 {filename}')

def export_c5_4way(hw, filename='lenet_c5_split.h'):
    w_c5 = hw['c5']['w'].reshape(4, 4, 16, 120)
    b_c5 = hw['c5']['b']
    segs = [(0, 32), (32, 64), (64, 96), (96, 120)]

    with open(filename, 'w') as f:
        f.write('// C5 split for W_DEPTH=8192\n')
        f.write('#ifndef LENET_C5_SPLIT_H\n#define LENET_C5_SPLIT_H\n\n')
        f.write('#include <stdint.h>\n\n')
        for i, (lo, hi) in enumerate(segs):
            name = f'c5{"abcd"[i]}'
            w = w_c5[:, :, :, lo:hi].flatten()
            b = b_c5[lo:hi]
            f.write(f'static const int8_t {name}_weight_hw[{w.size}] = {{\n')
            f.write(_fmt_i8(w) + '\n};\n')
            f.write(f'static const int32_t {name}_bias[{b.size}] = {{\n')
            f.write(_fmt_i32(b) + '\n};\n\n')
            print(f'  {name}: OC={hi-lo}, w={w.size}, b={b.size}')
        f.write('#endif\n')
    print(f'[OK] 导出到 {filename}')

# ============================================================
# 6. 32 位字打包
# ============================================================
LAYER_INFO = {
    'c1':  (5, 5, 1,   6),
    'c3':  (5, 5, 6,   16),
    'c5':  (4, 4, 16,  120),
    'f6':  (1, 1, 120, 84),
    'out': (1, 1, 84,  10),
}

def _s8(v):
    v &= 0xFF
    return v - 256 if v >= 128 else v

def export_coe(all_words, filename='rom_all_tap.coe', radix=16):
    n = len(all_words)
    with open(filename, 'w') as f:
        f.write(f'memory_initialization_radix={radix};\n')
        f.write('memory_initialization_vector=\n')
        for i, w in enumerate(all_words):
            if i == n - 1:
                f.write(f'{w & 0xFFFFFFFF:08x};\n')
            else:
                f.write(f'{w & 0xFFFFFFFF:08x},\n')
    print(f'[OK] {filename}: {n} words (coe for ROM IP)')

def pack_layer_words_oc_order(w_flat_int8, OC, align=4):
    n_groups = len(w_flat_int8) // OC
    words = []
    for g in range(n_groups):
        group = w_flat_int8[g*OC:(g+1)*OC].tolist()
        pad = (-OC) % align
        if pad:
            group = group + [0] * pad
        for i in range(0, len(group), align):
            v0, v1, v2, v3 = group[i], group[i+1], group[i+2], group[i+3]
            word = ((int(v3) & 0xFF) << 24) | \
                   ((int(v2) & 0xFF) << 16) | \
                   ((int(v1) & 0xFF) << 8)  | \
                    (int(v0) & 0xFF)
            words.append(word)
    return words

def export_rom_hex_oc_order(hw, filename='rom_all.hex',
                            coe_filename='rom_all.coe',
                            wbase_filename='wbase.h'):
    all_words = []
    offsets = {}
    for name in ['c1', 'c3', 'c5', 'f6', 'out']:
        KH, KW, C, OC = LAYER_INFO[name]
        w = hw[name]['w']
        offsets[name] = len(all_words)
        words = pack_layer_words_oc_order(w, OC, align=4)
        all_words.extend(words)
        print(f'  [OC ] {name:>3s}: KH={KH} KW={KW} C={C:>3d} OC={OC:>3d}  '
              f'w_base={offsets[name]:>6d}  words={len(words):>6d}')

    with open(filename, 'w') as f:
        for w in all_words:
            f.write(f'{w & 0xFFFFFFFF:08x}\n')
    total = len(all_words)
    print(f'[OK] {filename}: {total} words = {total*4} int8')

    export_coe(all_words, coe_filename)

    with open(wbase_filename, 'w') as f:
        f.write('// Auto-generated by lenet_export.py (OC-ORDER ROM)\n')
        f.write('// w_base is in 32-bit WORD units, not byte units\n')
        f.write('// Layout: [KH][KW][C][OC/4][LANE]\n')
        f.write('#ifndef WBASE_H\n#define WBASE_H\n\n')
        for name in ['c1', 'c3', 'c5', 'f6', 'out']:
            f.write(f'#define W_BASE_{name.upper():<4s} {offsets[name]:>6d}u\n')
        f.write(f'\n#define W_TOTAL_WORDS {total}u\n')
        f.write('\n#endif\n')
    print(f'[OK] {wbase_filename} 已导出')

    return offsets, total

def pack_layer_words_tap_order(w_flat_int8, K, C, OC, align=4):
    KKC = K * K * C
    num_tap_groups = (KKC + align - 1) // align

    words = []
    for oc in range(OC):
        for tg in range(num_tap_groups):
            group = []
            for lane in range(align):
                tap = tg * align + lane
                if tap < KKC:
                    kh  = tap // (K * C)
                    rem = tap %  (K * C)
                    kw  = rem // C
                    c   = rem %  C
                    idx = ((kh * K + kw) * C + c) * OC + oc
                    group.append(int(w_flat_int8[idx]))
                else:
                    group.append(0)
            v0, v1, v2, v3 = group
            word = ((int(v3) & 0xFF) << 24) | \
                   ((int(v2) & 0xFF) << 16) | \
                   ((int(v1) & 0xFF) << 8)  | \
                    (int(v0) & 0xFF)
            words.append(word)
    return words

def export_rom_hex_tap_order(hw, filename='rom_all_tap.hex',
                             coe_filename='rom_all_tap.coe',
                             wbase_filename='wbase_tap.h'):
    all_words = []
    offsets = {}
    for name in ['c1', 'c3', 'c5', 'f6', 'out']:
        KH, KW, C, OC = LAYER_INFO[name]
        K = KH
        w = hw[name]['w']
        offsets[name] = len(all_words)
        words = pack_layer_words_tap_order(w, K, C, OC, align=4)
        all_words.extend(words)
        print(f'  [TAP] {name:>3s}: K={K} C={C:>3d} OC={OC:>3d}  '
              f'w_base={offsets[name]:>6d}  words={len(words):>6d}  '
              f'(num_tap_groups={(K*K*C+3)//4})')

    with open(filename, 'w') as f:
        for w in all_words:
            f.write(f'{w & 0xFFFFFFFF:08x}\n')
    total = len(all_words)
    print(f'[OK] {filename}: {total} words = {total*4} int8')

    export_coe(all_words, coe_filename)

    with open(wbase_filename, 'w') as f:
        f.write('// Auto-generated by lenet_export.py (TAP-ORDER ROM)\n')
        f.write('// w_base is in 32-bit WORD units, not byte units\n')
        f.write('// Layout: [OC][TAP_GROUP][LANE]\n')
        f.write('// word_addr = W_BASE_xxx + oc * num_tap_groups + tap_group\n')
        f.write('#ifndef WBASE_TAP_H\n#define WBASE_TAP_H\n\n')
        for name in ['c1', 'c3', 'c5', 'f6', 'out']:
            KH, KW, C, OC = LAYER_INFO[name]
            K = KH
            ntg = (K*K*C + 3) // 4
            f.write(f'#define W_BASE_{name.upper():<4s} {offsets[name]:>6d}u   // num_tap_groups={ntg}\n')
        f.write(f'\n#define W_TOTAL_WORDS_TAP {total}u\n')
        f.write('\n#endif\n')
    print(f'[OK] {wbase_filename} 已导出')

    return offsets, total

# ============================================================
# 7. 模拟 C 端推理（加饱和 + 逐层打印）
# ============================================================
def infer_int8(model_q, img_uint8):
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
    if DEBUG_LAYER:
        print("PY C1[0:8]:", x.flatten()[:8].tolist())

    # ---- S2 池化：2x2 max ----
    x = np.maximum(
            np.maximum(x[:, 0::2, 0::2], x[:, 0::2, 1::2]),
            np.maximum(x[:, 1::2, 0::2], x[:, 1::2, 1::2]))
    if DEBUG_LAYER:
        print("PY S2[0:8]:", x.flatten()[:8].tolist())

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
    if DEBUG_LAYER:
        print("PY C3[0:8]:", x.flatten()[:8].tolist())

    # ---- S4 池化：2x2 max ----
    x = np.maximum(
            np.maximum(x[:, 0::2, 0::2], x[:, 0::2, 1::2]),
            np.maximum(x[:, 1::2, 0::2], x[:, 1::2, 1::2]))
    if DEBUG_LAYER:
        print("PY S4[0:8]:", x.flatten()[:8].tolist())

    # ---- C5 ----
    flat = x.flatten().astype(np.int32)
    q = model_q['c5']
    w = q['w'].reshape(120, 256).astype(np.int32)
    b = q['b'].astype(np.int32)
    y = np.zeros(120, dtype=np.int32)
    for i in range(120):
        y[i] = min(max((b[i] + np.dot(w[i], flat)) >> QUANT_SHIFT, 0), 127)
    if DEBUG_LAYER:
        print("PY C5[0:8]:", y[:8].tolist())

    # ---- F6 ----
    q = model_q['f6']
    w = q['w'].reshape(84, 120).astype(np.int32)
    b = q['b'].astype(np.int32)
    z = np.zeros(84, dtype=np.int32)
    for i in range(84):
        z[i] = min(max((b[i] + np.dot(w[i], y)) >> QUANT_SHIFT, 0), 127)
    if DEBUG_LAYER:
        print("PY F6[0:8]:", z[:8].tolist())

    # ---- OUT ----
    q = model_q['out']
    w = q['w'].reshape(10, 84).astype(np.int32)
    b = q['b'].astype(np.int32)
    out = np.zeros(10, dtype=np.int32)
    for i in range(10):
        out[i] = b[i] + np.dot(w[i], z)
    if DEBUG_LAYER:
        print("PY OUT[0:10]:", out[:10].tolist())

    return out

def eval_int8_accuracy(model_q, test_set, n=100):
    global DEBUG_LAYER
    old = DEBUG_LAYER
    DEBUG_LAYER = False
    correct = 0
    for i in range(n):
        img = (test_set[i][0][0].numpy() * 255).astype(np.uint8)
        label = int(test_set[i][1])
        scores = infer_int8(model_q, img)
        pred = int(np.argmax(scores))
        if pred == label:
            correct += 1
    DEBUG_LAYER = old
    print(f'  infer_int8 前 {n} 张准确率: {100.0 * correct / n:.2f}%')
    return correct / n

# ============================================================
# 8. Roundtrip 校验
# ============================================================
def verify_oc_order(hw):
    print('\nVerifying OC-order packing roundtrip...')
    ok = True
    for name in ['c1', 'c3', 'c5', 'f6', 'out']:
        KH, KW, C, OC = LAYER_INFO[name]
        w = hw[name]['w']
        words = pack_layer_words_oc_order(w, OC, align=4)
        unpacked = []
        for wd in words:
            unpacked.append(_s8(wd))
            unpacked.append(_s8(wd >> 8))
            unpacked.append(_s8(wd >> 16))
            unpacked.append(_s8(wd >> 24))
        idx = 0
        for g in range(len(w) // OC):
            for i in range(OC):
                if unpacked[idx + i] != w[g*OC + i]:
                    print(f'  MISMATCH {name} at group {g} idx {i}: '
                          f'packed={unpacked[idx+i]} orig={w[g*OC+i]}')
                    ok = False
            idx += OC + ((-OC) % 4)
    print('  OC-order Roundtrip OK' if ok else '  OC-order Roundtrip FAILED')
    return ok

def verify_tap_order(hw):
    print('\nVerifying TAP-order packing roundtrip...')
    ok = True
    for name in ['c1', 'c3', 'c5', 'f6', 'out']:
        KH, KW, C, OC = LAYER_INFO[name]
        K = KH
        w = hw[name]['w']
        words = pack_layer_words_tap_order(w, K, C, OC, align=4)

        KKC = K * K * C
        num_tap_groups = (KKC + 3) // 4
        unpacked = []
        for wd in words:
            unpacked.append(_s8(wd))
            unpacked.append(_s8(wd >> 8))
            unpacked.append(_s8(wd >> 16))
            unpacked.append(_s8(wd >> 24))

        pos = 0
        for oc in range(OC):
            for tg in range(num_tap_groups):
                for lane in range(4):
                    tap = tg * 4 + lane
                    if tap < KKC:
                        kh  = tap // (K * C)
                        rem = tap %  (K * C)
                        kw  = rem // C
                        c   = rem %  C
                        idx = ((kh * K + kw) * C + c) * OC + oc
                        if unpacked[pos] != w[idx]:
                            print(f'  MISMATCH {name} oc={oc} tap={tap} '
                                  f'packed={unpacked[pos]} orig={w[idx]}')
                            ok = False
                    pos += 1
    print('  TAP-order Roundtrip OK' if ok else '  TAP-order Roundtrip FAILED')
    return ok

# ============================================================
# 9. 主流程
# ============================================================
def main():
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    print(f'Using device: {device}')

    transform = transforms.Compose([transforms.ToTensor()])
    train_set = datasets.MNIST('./data', train=True,  download=True, transform=transform)
    test_set  = datasets.MNIST('./data', train=False, download=True, transform=transform)

    train_loader = DataLoader(train_set, batch_size=64,  shuffle=True)
    test_loader  = DataLoader(test_set,  batch_size=1000, shuffle=False)

    model = LeNet5().to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)

    EPOCHS = 5
    for epoch in range(1, EPOCHS + 1):
        train_one_epoch(model, device, train_loader, optimizer)
        acc = evaluate(model, device, test_loader)
        print(f'Epoch {epoch}: test acc = {acc:.2f}%')

    print('\nQuantizing model...')
    qmodel = quantize_model(model, S_IN)

    print('\nExporting SOFTWARE layout...')
    export_c_header_sw(qmodel, 'lenet_weights.h')

    print('\nTransposing to HARDWARE layout...')
    hw = transpose_to_hw(qmodel)

    print('')
    export_c_header_hw(hw, 'lenet_weights_hw.h')

    export_c5_4way(hw, 'lenet_c5_split.h')

    print('\n=== Packing weights (OC order) ===')
    export_rom_hex_oc_order(hw,
                            filename='rom_all.hex',
                            coe_filename='rom_all.coe',
                            wbase_filename='wbase.h')

    print('\n=== Packing weights (TAP order) ===')
    export_rom_hex_tap_order(hw,
                             filename='rom_all_tap.hex',
                             coe_filename='rom_all_tap.coe',
                             wbase_filename='wbase_tap.h')

    print('\nVerifying INT8 inference on a sample digit 3...')
    for data, target in test_loader:
        idx = (target == 3).nonzero(as_tuple=True)[0]
        if len(idx) > 0:
            img = (data[idx[0], 0].numpy() * 255).astype(np.uint8)
            scores = infer_int8(qmodel, img)
            print('  real label = 3, predicted =', int(np.argmax(scores)))
            print('  scores =', scores.tolist())
            break

    print('\nEvaluating infer_int8 accuracy on first 100 test images...')
    eval_int8_accuracy(qmodel, test_set, n=100)

    verify_oc_order(hw)
    verify_tap_order(hw)

    # ============================================================
    # 保存量化模型，供 test_one.py 使用
    # ============================================================
    with open('qmodel.pkl', 'wb') as f:
        pickle.dump(qmodel, f)
    print('[OK] qmodel.pkl 已保存')

    print('\nDone.')

if __name__ == '__main__':
    main()