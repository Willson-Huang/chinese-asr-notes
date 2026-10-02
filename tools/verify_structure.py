#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""结构校验：确认产物符合 14 节知识库条目格式。

背景：曾出现连续两批产物退回 6 节旧模板（无 frontmatter、无关键实体表、
无反共识观点等），且缺少 tags/entities 导致检索能力归零。本脚本做硬拦截。

用法:
  python verify_structure.py <文件1> [文件2 ...]
  python verify_structure.py --dir raw          # 校验整个目录

校验四项:
  1. frontmatter 存在且含必需字段（title/date/type/source/tags/keywords/entities/
     confidence/review_by）
  2. tags 的每一项必须带 src/ topic/ entity/ 前缀（知识库 schema 第 4 节：不带前缀的
     标签一律无效）；entities 8-12 个（模板粒度要求，决定半年后能否搜到）
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
REQUIRED_FM = ['title', 'date', 'type', 'source', 'tags', 'keywords', 'entities',
               'confidence', 'review_by']
NS = ('src/', 'topic/', 'entity/')
# 封闭枚举（知识库 schema 第 3 节）；本脚本只处理 _纪要.md，故转写类 4 值为常用项
TYPE_ENUM = {'转写·访谈', '转写·科普', '转写·评论', '转写·教程', '公众号', '政策原文'}


def _yaml_list(block, key):
    """取 frontmatter 里某个列表字段的条目，返回 (条目列表, 形态)。

    形态 inline  = 写在同一行的 [a, b, c]
    形态 block   = 另起缩进行，形如 tags: 换行后跟 '  - a'
    形态 missing = 两种写法都匹配不到。这时必须出声，否则粒度要求会被静默跳过。
    """
    m = re.search(rf'^{key}:\s*\[(.*?)\]', block, re.M)
    if m:
        return [x.strip() for x in m.group(1).split(',') if x.strip()], 'inline'
    m = re.search(rf'^{key}:[ \t]*\r?\n((?:[ \t]+-.*\r?\n?)+)', block, re.M)
    if m:
        items = [ln.strip()[1:].strip() for ln in m.group(1).splitlines()
                 if ln.strip().startswith('-')]
        return [x for x in items if x], 'block'
    return [], 'missing'


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
        # type 是封闭枚举：自创值（如旧模板的「播客访谈纪要」）进库即判不合规
        mt = re.search(r'^type:\s*(\S+)', block, re.M)
        if mt and mt.group(1) not in TYPE_ENUM:
            errors.append(f'`type` 不在枚举表内: {mt.group(1)}'
                          f'（可选值 {" / ".join(sorted(TYPE_ENUM))}）')
        tags_items, tags_style = _yaml_list(block, 'tags')
        ent_items, ent_style = _yaml_list(block, 'entities')
        tags_n, ent_n = len(tags_items), len(ent_items)
        # tags 改查「带命名空间前缀」而不是个数（2026-09-28）：
        # 本库 tags 已分层——只放可导航的桶名（src/ topic/ entity/），
        # 原有具体词全量进 keywords。仍按旧规则要求 6-10 个会逼出无前缀标签，
        # 而这类标签进库会被 check_frontmatter.py 判为不合规。
        if tags_style == 'missing':
            warnings.append('tags 读不出条目（既不是内联数组也不是块状列表），'
                            '本项无法校验')
        else:
            badns = [x for x in tags_items if not x.startswith(NS)]
            if badns:
                warnings.append('tags 含无命名空间前缀项: ' + ', '.join(badns[:5]))
        if ent_style == 'missing':
            warnings.append('entities 读不出条目（既不是内联数组也不是块状列表），'
                            '本项无法校验')
        elif not (8 <= ent_n <= 12):
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
    log_path = Path.cwd() / 'verify_structure_out.txt'
    real = sys.stdout
    with open(log_path, 'w', encoding='utf-8') as f:
        sys.stdout = f
        try:
            code = main()
        except SystemExit as e:
            code = e.code
        finally:
            sys.stdout = real
    print(f'详细结果写入 {log_path}')
    for line in log_path.read_text(encoding='utf-8').splitlines()[-6:]:
        print('  ' + line)
    sys.exit(code)
