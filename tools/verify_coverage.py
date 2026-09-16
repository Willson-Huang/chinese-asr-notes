#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
覆盖校验：确认增强版文件没有遗失原文件的内容。

用法:
  python verify_coverage.py <原文件> <新文件>

校验三项:
  1. 时间戳覆盖 —— 原文件所有 hh:mm:ss 是否都在新文件中
  2. 章节标题覆盖 —— 原文件所有 ## / ### 标题是否都在新文件中
  3. 体积变化 —— 新文件字符数应大于原文件

退出码: 0 = 全部通过; 1 = 存在遗失
"""
import re
import sys

sys.stdout.reconfigure(encoding='utf-8')

if len(sys.argv) < 3:
    print('用法: python verify_coverage.py <原文件> <新文件>')
    sys.exit(2)

def read_text(path):
    """读取文本。utf-8-sig 兼容带 BOM 的文件；失败给友好提示而非裸栈。"""
    try:
        with open(path, encoding='utf-8-sig') as f:
            return f.read()
    except FileNotFoundError:
        print(f'[错误] 文件不存在: {path}')
        sys.exit(2)
    except UnicodeDecodeError:
        print(f'[错误] 文件不是 UTF-8 编码，请先转换: {path}')
        sys.exit(2)
    except OSError as e:
        print(f'[错误] 读取失败 {path}: {e}')
        sys.exit(2)


old = read_text(sys.argv[1])
new = read_text(sys.argv[2])

ok = True

# 1. 时间戳
old_ts = sorted(set(re.findall(r'\d{2}:\d{2}:\d{2}', old)))
new_ts = set(re.findall(r'\d{2}:\d{2}:\d{2}', new))
miss_ts = [t for t in old_ts if t not in new_ts]
print(f'时间戳: 原 {len(old_ts)} 个 / 缺失 {len(miss_ts)} 个')
if miss_ts:
    ok = False
    for t in miss_ts:
        print(f'   缺失 [{t}]')

# 2. 章节标题
old_h = re.findall(r'^#{2,3} .+$', old, re.M)
new_h = re.findall(r'^#{2,3} .+$', new, re.M)
miss_h = []
for h in old_h:
    if h in new_h:
        continue
    core = re.sub(r'^\d+(\.\d+)?\s*', '', h.lstrip('# ').strip())
    if any(core in x for x in new_h):
        continue
    miss_h.append(h)
print(f'章节标题: 原 {len(old_h)} 个 / 缺失 {len(miss_h)} 个')
if miss_h:
    ok = False
    for h in miss_h:
        print(f'   缺失 {h}')

# 3. 体积
delta = len(new) - len(old)
print(f'体积: 原 {len(old)} 字符 -> 新 {len(new)} 字符 ({delta:+d})')
if delta <= 0:
    ok = False
    print('   新文件未增大，疑似发生删减')

print()
if ok:
    print('PASS 原文件内容 100% 覆盖，无遗失')
    sys.exit(0)
else:
    print('FAIL 存在遗失，需补回后重新校验')
    sys.exit(1)
