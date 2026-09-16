#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""结构校验：确认产物符合 14 节知识库条目格式。

背景：曾出现连续两批产物退回 6 节旧模板（无 frontmatter、无关键实体表、
无反共识观点等），且缺少 tags/entities 导致检索能力归零。本脚本做硬拦截。

用法:
  python verify_structure.py <文件1> [文件2 ...]
  python verify_structure.py --dir raw          # 校验整个目录

校验四项:
  1. frontmatter 存在且含必需字段（title/date/type/source/tags/entities/confidence/review_by）
  2. tags 6-10 个、entities 8-12 个（模板粒度要求，决定半年后能否搜到）
  3. 14 节标题齐全（一～十四，按序）
  4. 内容要点以表格为主（模板：能用表格就不用散文）

退出码: 0 = 全部通过; 1 = 存在不合规; 2 = 参数错误
"""
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

CN = ['一', '二', '三', '四', '五', '六', '七', '八', '九', '十',
      '十一', '十二', '十三', '十四']
REQUIRED_FM = ['title', 'date', 'type', 'source', 'tags', 'entities',
               'confidence', 'review_by']


def check(path: Path):
    """返回 (errors, warnings, 节数, tags数, entities数)

    errors   = 结构缺陷，必须修（缺 frontmatter / 缺字段 / 缺节）
    warnings = 粒度偏离指引，建议修（数量超范围 / 表格偏少）
    """
    t = path.read_text(encoding='utf-8', errors='replace')
    errors, warnings = [], []

    # 1 + 2. frontmatter
    fm = re.match(r'^---\n(.*?)\n---', t, re.S)
    if not fm:
        errors.append('缺少 frontmatter（tags/entities 决定检索能力）')
        tags_n = ent_n = 0
    else:
        block = fm.group(1)
        miss = [k for k in REQUIRED_FM
                if not re.search(rf'^{k}:\s*\S', block, re.M)]
        if miss:
            errors.append(f'frontmatter 缺字段: {", ".join(miss)}')
        # tags
        m = re.search(r'^tags:\s*\[(.*?)\]', block, re.M)
        tags = [x.strip() for x in m.group(1).split(',') if x.strip()] if m else []
        tags_n = len(tags)
        if m and not (6 <= tags_n <= 10):
            warnings.append(f'tags {tags_n} 个（指引 6-10）')
        # entities
        m = re.search(r'^entities:\s*\[(.*?)\]', block, re.M)
        ents = [x.strip() for x in m.group(1).split(',') if x.strip()] if m else []
        ent_n = len(ents)
        if m and not (8 <= ent_n <= 12):
            warnings.append(f'entities {ent_n} 个（指引 8-12）')

    # 3. 14 节
    heads = re.findall(r'^##\s*(.+)$', t, re.M)
    have = {}
    for h in heads:
        m = re.match(r'^([一二三四五六七八九十]+|十一|十二|十三|十四)、', h.strip())
        if m and m.group(1) in CN:
            have[m.group(1)] = h.strip()
    miss_sec = [c for c in CN if c not in have]
    if miss_sec:
        errors.append(f'缺 {len(miss_sec)} 节: ' + '、'.join(miss_sec))

    # 4. 内容要点表格化（仅当有该节时检查）
    sec4 = have.get('四')
    if sec4:
        i = t.find('## ' + sec4)
        j = t.find('\n## ', i + 5)
        body = t[i:j] if j > 0 else t[i:]
        if body.count('|') < 20:
            warnings.append('四、内容要点表格偏少（模板：能用表格就不用散文）')

    return errors, warnings, len(heads), tags_n, ent_n


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if '--dir' in sys.argv:
        d = Path(sys.argv[sys.argv.index('--dir') + 1])
        files = sorted(d.glob('*.md'))
    elif args:
        files = [Path(a) for a in args]
    else:
        print('用法: python verify_structure.py <文件...>  |  --dir <目录>')
        sys.exit(2)

    VIDEO_TYPES = ['播客访谈纪要', '知识科普', '评论解说', '教程演示', '圆桌对谈']

    bad = warn_n = skip = 0
    rows = []
    for f in files:
        if not f.exists():
            print(f'[跳过] {f} 不存在')
            continue
        # 非视频条目（政策原文等）不适用本模板：按文件名后缀判断
        # 不用 type 字段过滤——type 值写得太具体（科技测评/社会评论/演讲…）会误跳过真条目
        if not f.name.endswith('_纪要.md'):
            skip += 1
            print(f'- {f.name[:58]} (跳过，非视频条目)')
            continue
        errors, warnings, nh, tn, en = check(f)
        rows.append((f.name, errors, warnings, nh, tn, en))
        if errors:
            bad += 1
        elif warnings:
            warn_n += 1

    for name, errors, warnings, nh, tn, en in rows:
        if errors:
            print(f'✗ {name[:62]}')
            for i in errors:
                print(f'    [错误] {i}')
            for i in warnings:
                print(f'    [提醒] {i}')
        elif warnings:
            print(f'⚠ {name[:62]}')
            for i in warnings:
                print(f'    [提醒] {i}')
        else:
            print(f'✓ {name[:62]}  ({nh} 节, tags {tn}, entities {en})')

    clean = len(rows) - bad - warn_n
    print(f'\n合计 {len(rows)} 份视频条目（另跳过非视频 {skip} 份）：'
          f'合规 {clean} / 仅粒度提醒 {warn_n} / 结构缺陷 {bad}')
    if bad:
        print(f'\n需修复（结构缺陷，共 {bad} 份）：')
        for name, errors, _, _, _, _ in rows:
            if errors:
                print(f'  - {name[:66]}')
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
