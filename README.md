# dirsize

看看磁盘空间都去哪了 —— 纯标准库的终端磁盘用量可视化。

```bash
$ python -m dirsize ~/workspace --depth 2
3.2 GB  /home/hatch/workspace
共 412 个文件，38 个子目录
├─ 1.1 GB ████████████████████ ai-forks
├─ 860.4 MB ████████████████    media_library
├─ 120.0 MB ██                old-builds
└─ 4.0 KB                     empty-dir
```

## 安装

零依赖，Python 3.8+ 直接跑：

```bash
python -m dirsize [目录] [选项]
```

## 用法

| 命令 | 说明 |
|---|---|
| `dirsize ~/workspace --depth 2` | 目录树 + 比例条（条按同级最大缩放） |
| `dirsize . --top 10` | 列出全局最大的 10 个文件/目录 |
| `dirsize . --min-size 100M` | 只显示 ≥100M 的条目 |
| `dirsize . --exclude "node_modules\|.git"` | 排除目录名模式（`\|` 分隔，fnmatch） |
| `dirsize . --json` | 输出 JSON（给脚本消费） |
| `dirsize --version` | 版本 |

## 统计口径（诚实说明）

- 计的是文件**表观大小**（apparent size，文件内容的字节数），**不是**
  磁盘实际占用的块。和 `du -sb` 接近，但不含目录项本身的元数据开销，
  所以通常略小于 `du -sb` 的结果。
- **目录符号链接不递归**（防止循环），只显示链接本身并标注 `[链接]`；
  **文件符号链接**跟随一次取目标大小；同一个 inode 全局只计一次。
- **硬链接会被重复计算**（每个路径计一次），这是已知取舍。
- 无权限的目录/文件跳过并在 stderr 警告一次，不中断扫描。
- 稀疏文件按表观大小计（`du` 默认按实际占用计，两者会有差异）。

## License

MIT
