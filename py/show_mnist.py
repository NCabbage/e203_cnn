import torch
import matplotlib.pyplot as plt
from torchvision import datasets, transforms

# 加载 MNIST 测试集
test_set = datasets.MNIST('./data', train=False, download=True,
                          transform=transforms.ToTensor())

# 找出 0-9 各一张，画成 2x5 的网格
found = {}
for img, label in test_set:
    label = int(label)
    if label not in found:
        found[label] = img[0].numpy()
    if len(found) == 10:
        break

fig, axes = plt.subplots(2, 5, figsize=(10, 4))
for d in range(10):
    ax = axes[d // 5][d % 5]
    ax.imshow(found[d], cmap='gray')
    ax.set_title(f'Label: {d}')
    ax.axis('off')

plt.tight_layout()
plt.show()