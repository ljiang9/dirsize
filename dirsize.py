#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dirsize — 看看磁盘空间都去哪了。

纯标准库的终端磁盘用量可视化：目录树 + 比例条 + Top N 大文件/目录。

统计口径（诚实说明）：
- 计的是文件"表观大小"（apparent size，即文件内容的字节数），
  不是磁盘实际占用的块（block）。和 `du -sb` 接近，但不含目录项
  本身的元数据开销，所以通常略小于 `du -sb` 的结果。
- 目录符号链接：不递归（防止循环），只显示链接本身，标注 [链接]。
- 文件符号链接：跟随一次取目标大小；同一个 inode 全局只计一次
  （硬链接同理），避免重复计算。
- 无权限的目录/文件：跳过并在 stderr 警告一次，不中断扫描。
"""

import argparse
import fnmatch
import json
import os
import sys

VERSION = "0.1.0"
BAR_WIDTH = 20
BAR_CHAR = "\u2588"  # █


def parse_size(text):
    """解析 '100M' / '1.5G' / '512' 为字节数。"""
    text = text.strip().upper()
    mult = {"TB": 1024 ** 4, "GB": 1024 ** 3, "MB": 1024 ** 2, "KB": 1024,
            "T": 1024 ** 4, "G": 1024 ** 3, "M": 1024 ** 2, "K": 1024, "B": 1}
    for suffix in sorted(mult, key=len, reverse=True):
        if text.endswith(suffix) and len(text) > len(suffix):
            try:
                return int(float(text[:-len(suffix)]) * mult[suffix])
            except ValueError:
                break
    try:
        return int(float(text))
    except ValueError:
        raise ValueError("无法解析大小：%s" % text)


def human(n):
    """字节数 -> 人类可读。"""
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if n < 1024 or unit == "PB":
            if unit == "B":
                return "%d B" % n
            return "%.1f %s" % (n, unit)
        n /= 1024


class DirNode:
    __slots__ = ("path", "name", "size", "n_files", "n_dirs",
                 "children", "symlink", "target")

    def __init__(self, path, name):
        self.path = path
        self.name = name
        self.size = 0
        self.n_files = 0
        self.n_dirs = 0
        self.children = []
        self.symlink = False
        self.target = None


class Scanner:
    def __init__(self, exclude):
        self.exclude = [p for p in exclude.split("|") if p] if exclude else []
        self.seen = set()      # 已计数的 (st_dev, st_ino)，防重复
        self.warnings = []     # 权限等问题
        self.files = []        # (path, size) 全部文件，供 --top 用

    def excluded(self, name):
        return any(fnmatch.fnmatch(name, pat) for pat in self.exclude)

    def warn(self, msg):
        self.warnings.append(msg)

    def _count_file(self, node, path, st):
        key = (st.st_dev, st.st_ino)
        if key in self.seen:
            return
        self.seen.add(key)
        node.size += st.st_size
        node.n_files += 1
        self.files.append((path, st.st_size))

    def scan(self, path, name):
        node = DirNode(path, name)
        try:
            lst = os.lstat(path)
        except OSError as e:
            self.warn("无法访问 %s：%s" % (path, e.strerror or e))
            return node
        if os.path.islink(path):
            # 目录符号链接：不递归，只记链接本身
            node.symlink = True
            try:
                node.target = os.readlink(path)
            except OSError:
                pass
            node.size = lst.st_size
            return node
        try:
            entries = list(os.scandir(path))
        except OSError as e:
            self.warn("无法读取目录 %s：%s" % (path, e.strerror or e))
            return node
        for entry in entries:
            if self.excluded(entry.name):
                continue
            try:
                if entry.is_symlink():
                    self._add_symlink(node, entry)
                elif entry.is_dir(follow_symlinks=False):
                    child = self.scan(entry.path, entry.name)
                    node.children.append(child)
                    node.size += child.size
                    node.n_files += child.n_files
                    node.n_dirs += child.n_dirs + 1
                elif entry.is_file(follow_symlinks=False):
                    try:
                        self._count_file(node, entry.path,
                                         entry.stat(follow_symlinks=False))
                    except OSError as e:
                        self.warn("无法读取 %s：%s" % (entry.path, e.strerror or e))
                # fifo / socket / device 等特殊文件：忽略
            except OSError as e:
                self.warn("无法访问 %s：%s" % (entry.path, e.strerror or e))
        node.children.sort(key=lambda c: c.size, reverse=True)
        return node

    def _add_symlink(self, node, entry):
        """符号链接：目录链接不递归；文件链接跟随一次取大小；坏链记链接本身。"""
        try:
            if entry.is_dir(follow_symlinks=True):
                child = DirNode(entry.path, entry.name)
                child.symlink = True
                try:
                    child.target = os.readlink(entry.path)
                except OSError:
                    pass
                try:
                    child.size = os.lstat(entry.path).st_size
                except OSError:
                    pass
                node.children.append(child)
                node.n_dirs += 1
                return
        except OSError:
            pass
        try:
            st = entry.stat(follow_symlinks=True)  # 文件链接：跟随一次
            # files 榜按解析后的真实路径记录，避免 scandir 顺序导致榜上显示的是链接名
            self._count_file(node, os.path.realpath(entry.path), st)
        except OSError:
            try:  # 坏链：只记链接本身大小
                self._count_file(node, entry.path,
                                 entry.stat(follow_symlinks=False))
            except OSError as e:
                self.warn("无法读取 %s：%s" % (entry.path, e.strerror or e))


def render_tree(node, max_depth, min_size):
    lines = []
    lines.append("%s  %s" % (human(node.size), node.path))
    lines.append("共 %d 个文件，%d 个子目录" % (node.n_files, node.n_dirs))

    def walk(n, prefix, depth):
        if depth >= max_depth:
            return
        kids = [c for c in n.children if c.size >= min_size]
        if not kids:
            return
        peak = max(c.size for c in kids) or 1
        for i, c in enumerate(kids):
            last = i == len(kids) - 1
            branch = "\u2514\u2500 " if last else "\u251c\u2500 "
            if c.size > 0:
                bar = BAR_CHAR * max(1, round(c.size / peak * BAR_WIDTH))
            else:
                bar = ""
            tag = " [链接]" if c.symlink else ""
            lines.append("%s%s%-9s %s%s%s" %
                         (prefix, branch, human(c.size), bar, c.name, tag))
            walk(c, prefix + ("    " if last else "\u2502   "), depth + 1)

    walk(node, "", 0)
    return "\n".join(lines)


def collect_top(scanner, root, n):
    entries = [(p, s, "文件") for p, s in scanner.files]

    def walk(nd):
        entries.append((nd.path, nd.size, "目录" + ("[链接]" if nd.symlink else "")))
        for c in nd.children:
            walk(c)

    walk(root)
    entries.sort(key=lambda t: t[1], reverse=True)
    return entries[:n]


def node_to_dict(node):
    return {
        "name": node.name,
        "path": node.path,
        "size_bytes": node.size,
        "files": node.n_files,
        "dirs": node.n_dirs,
        "symlink": node.symlink,
        "target": node.target,
        "children": [node_to_dict(c) for c in node.children],
    }


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="dirsize",
        description="看看磁盘空间都去哪了：目录树 + 比例条 + Top N 大文件/目录（纯标准库）。")
    ap.add_argument("path", nargs="?", default=".",
                    help="要扫描的目录（默认当前目录）")
    ap.add_argument("--depth", type=int, default=2, help="目录树显示深度（默认 2）")
    ap.add_argument("--top", type=int, default=0, metavar="N",
                    help="列出全局最大的 N 个文件/目录")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--min-size", default="0",
                    help="只显示不小于该大小的条目，如 100M")
    ap.add_argument("--exclude", default="",
                    help='排除目录名模式，用 | 分隔，如 "node_modules|.git"')
    ap.add_argument("--version", action="store_true", help="显示版本")
    args = ap.parse_args(argv)

    if args.version:
        print("dirsize " + VERSION)
        return 0

    target = os.path.abspath(os.path.expanduser(args.path))
    if not os.path.isdir(target):
        sys.stderr.write("error: 目录不存在：%s\n" % args.path)
        return 1
    try:
        min_size = parse_size(args.min_size)
    except ValueError as e:
        sys.stderr.write("error: %s\n" % e)
        return 1

    scanner = Scanner(args.exclude)
    root = scanner.scan(target, os.path.basename(target) or target)

    if scanner.warnings:
        sys.stderr.write("警告：%s" % scanner.warnings[0])
        if len(scanner.warnings) > 1:
            sys.stderr.write("（另有 %d 处类似问题，已跳过）" % (len(scanner.warnings) - 1))
        sys.stderr.write("\n")

    if args.json:
        out = {
            "root": target,
            "total_bytes": root.size,
            "files": root.n_files,
            "dirs": root.n_dirs,
            "tree": node_to_dict(root),
            "top": [{"path": p, "size_bytes": s, "type": t}
                    for p, s, t in collect_top(scanner, root, args.top or 10)],
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    print(render_tree(root, args.depth, min_size))
    if args.top:
        print("\n===== 最大的 %d 个文件/目录 =====" % args.top)
        for i, (p, s, t) in enumerate(collect_top(scanner, root, args.top), 1):
            print("%2d. %-9s %-8s %s" % (i, human(s), t, p))
    return 0


if __name__ == "__main__":
    sys.exit(main())
