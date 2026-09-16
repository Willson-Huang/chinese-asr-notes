#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""素材包校验：确认转写产物符合「可回查」要求，归档前拦截。

素材包 = 一次转写的完整产出（Markdown）：标题与基本信息 + 带 [hh:mm:ss] 的正文
+ 机器可读元信息块。它是误识修正与专名纠错的唯一依据，字段残缺会让追溯能力断掉，
所以把问题拦在归档之前。本脚本只报告，不改文件。

用法:
  python verify_pack.py <文件1> [文件2 ...]
  python verify_pack.py --dir <素材包目录>          # 校验整个目录
  python verify_pack.py --dir <目录> --strict       # 新产物姿态：缺元信息块/无正文且无元信息块也判错

校验项:
  1. 标题行 + ## 基本信息 齐全（标题/发布/链接为必填；BV号、UP主 等平台字段存在才校验）
  2. 正文形态三选一且自洽：字幕全文 / 转写全文（正文行必须带 [hh:mm:ss] 前缀）/ 预扫描包（本就无正文）
  3. ## 元信息 块：8 个字段齐全、取值合法、route 与 engine 一致
  4. 确定性红线：元信息块不得含墙钟耗时或生成时间（会让包文本无法哈希回归）

宽容策略（重要）:
  默认模式把「缺元信息块」记为提醒，不判错——历史素材包本就没有该块；
  新产物请用 --strict（把缺块/位置异常也当错误）。

退出码: 0 = 全部通过（可含提醒）; 1 = 存在错误; 2 = 参数错误
"""
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

HEAD_RE = re.compile(r'^#\s*.*素材包')
META_FIELDS = ['route', 'engine', 'model', 'device', 'source',
               'ts_granularity', 'hotwords', 'audio_sec']
ENGINES = {'whisper', 'funasr-nano', 'subtitle', 'meta-only'}
GRANULARITY = {'cue', 'segment', 'segment(token)', '-'}
REQUIRED_BASE = ['标题', '发布', '链接']
OPTIONAL_BASE = ['BV号', 'UP主']  # 平台字段：存在即随包保留，缺失不判错
TS_LINE = re.compile(r'^\[\d{2}:\d{2}:\d{2}\] ')
TITLE_RE = re.compile(r'^- 标题：\S', re.M)
BV_RE = re.compile(r'^- BV号：(BV[0-9A-Za-z]{10})', re.M)
# 确定性红线：这些字段一旦进包，包文本就不再由输入唯一决定（哈希回归网会失效）
BANNED_META = ('download_sec', 'model_load_sec', 'asr_sec', 'generated', 'timestamp=')


def _section(text, head):
    """取某个 ## 节的内容（到下一个 ## 为止）；不存在返回 None"""
    i = text.find(head)
    if i < 0:
        return None
    j = text.find('\n## ', i + 3)
    return text[i:j] if j > 0 else text[i:]


def check_pack(path):
    """返回 (errors, warnings, info)

    errors   = 必须修（缺基本信息 / 正文无时间戳 / 元信息块字段不全或非法）
    warnings = 建议修（历史包缺元信息块、名称与 BV 不一致、位置异常、含墙钟耗时）
    info     = {'route','engine','ts_granularity','body_kind','body_lines'}
    """
    t = Path(path).read_text(encoding='utf-8', errors='replace')
    errors, warnings = [], []
    info = {'route': '-', 'engine': '-', 'ts_granularity': '-',
            'body_kind': 'meta-only', 'body_lines': 0, 'has_meta': False}

    # 1. 头部与基本信息
    first = t.split('\n', 1)[0].strip()
    if not HEAD_RE.match(first):
        errors.append(f'首行不是素材包标题行（应以 # 开头且含「素材包」，实际：{first[:40]}）')
    base = _section(t, '## 基本信息')
    if base is None:
        errors.append('缺 ## 基本信息 节')
    else:
        miss = [k for k in REQUIRED_BASE if f'- {k}：' not in base]
        if miss:
            errors.append('基本信息缺字段: ' + '、'.join(miss))
        if not TITLE_RE.search(t):
            errors.append('- 标题：为空')

    # 2. 正文形态（三选一：字幕全文 / 转写全文 / 预扫描包）
    kinds = re.findall(r'^## (字幕全文|转写全文)（', t, re.M)
    if len(kinds) > 1:
        errors.append(f'存在 {len(kinds)} 个正文节（一次只能走一条路由）: ' + '、'.join(kinds))
    for name, head in (('字幕全文', '## 字幕全文（'), ('转写全文', '## 转写全文（')):
        seg = _section(t, head)
        if seg is None:
            continue
        body = [l for l in seg.split('\n')[1:]
                if l.strip() and not l.startswith(('>', '#', '|'))]
        if not body:
            errors.append(f'{name} 节没有正文内容')
            continue
        # 判据取「所有正文行」而非「至少一行」：实测归档 115 份共 74302 行零例外，
        # 因此任何缺前缀的行都说明格式被破坏（只查 any() 会漏掉局部破坏）
        plain = [l for l in body if not TS_LINE.match(l)]
        if plain:
            errors.append(f'{name} 有 {len(plain)}/{len(body)} 行缺 [hh:mm:ss] 前缀'
                          f'（知识库依赖时间戳引用；例：{plain[0][:36]}）')
        if info['body_kind'] == 'meta-only':
            info['body_kind'] = name
            info['body_lines'] = len(body) - len(plain)

    # 3. 元信息块
    meta = _section(t, '## 元信息')
    if meta is None:
        warnings.append('缺 ## 元信息 块（早期历史产物本就没有该块；新产物请用 --strict 复检）')
    else:
        info['has_meta'] = True
        vals = {}
        for f in META_FIELDS:
            m = re.search(rf'^- {f}: (.*)$', meta, re.M)
            if not m or not m.group(1).strip():
                errors.append(f'元信息缺字段: {f}')
            else:
                vals[f] = m.group(1).strip()
        eng = vals.get('engine')
        if eng and eng not in ENGINES:
            errors.append(f'元信息 engine 取值非法: {eng}')
        gr = vals.get('ts_granularity')
        if gr and gr not in GRANULARITY:
            errors.append(f'元信息 ts_granularity 取值非法: {gr}')
        route = vals.get('route', '-')
        if eng and route != '-':
            # route 有两种形态：subtitle:<来源> / asr:<引擎>:<模型>@<设备>
            # ASR 路由的第一段是路由类型（asr），引擎名在第二段。
            # 2026-09-16 修：原判据 route.split(':', 1)[0] 取到的是 'asr'，
            # 导致所有 whisper / funasr-nano 素材包都被误报「route 与 engine 不一致」。
            parts = route.split(':')
            if parts[0] == 'asr':
                eng_in_route = parts[1] if len(parts) > 1 else ''
                bad = (eng_in_route != eng)
            else:
                bad = (parts[0] != eng)
            if bad:
                errors.append(f'元信息 route 与 engine 不一致（route={route} / engine={eng}）')
        # 位置：应在 基本信息 之后、正文之前（保证"开头即自描述"）
        if base is not None and t.find('## 元信息') < t.find('## 基本信息'):
            warnings.append('元信息块在基本信息之前（应为「开头即自描述」的固定位置）')
        hit = [b for b in BANNED_META if b in meta]
        if hit:
            warnings.append('元信息块含墙钟耗时/生成时间字段: ' + '、'.join(hit)
                            + '（会破坏包文本确定性，导致格式哈希回归失效）')
        # 形态自洽：有正文却没写 route / engine
        if info['body_kind'] != 'meta-only' and route == '-':
            errors.append('有正文但元信息 route 为空')
        info.update(route=route, engine=eng or '-', ts_granularity=gr or '-')

    # 5. 正文与元信息互证（归档目录实测 115 份全有正文，故"无正文"必须说清原因，不能静默放过）
    if not kinds:
        if info['engine'] == 'meta-only':
            pass                                     # 预扫描包：合法形态
        elif info['has_meta']:
            errors.append(f"元信息 engine={info['engine']} 声明有正文，但正文节不存在（包被截断？）")
        else:
            warnings.append('既无转写/字幕正文也无元信息块，无法区分预扫描包与正文被截断')
    elif info['engine'] == 'meta-only':
        errors.append('元信息 engine=meta-only 但存在正文节（形态自相矛盾）')

    # 4. 文件名与正文 BV 是否一致
    m = BV_RE.search(t)
    mf = re.search(r'BV[0-9A-Za-z]{10}', Path(path).stem)
    if m and mf and m.group(1) != mf.group(0):
        warnings.append(f'文件名 BV({mf.group(0)}) 与正文 BV({m.group(1)}) 不一致')

    return errors, warnings, info


def main():
    argv = sys.argv[1:]
    strict = '--strict' in argv
    args = [a for a in argv if not a.startswith('--')]
    if '--dir' in argv:
        d = Path(argv[argv.index('--dir') + 1])
        files = sorted(d.glob('*.md'))
    elif args:
        files = [Path(a) for a in args]
    else:
        print('用法: python verify_pack.py <文件...>  |  --dir <目录> [--strict]')
        sys.exit(2)

    rows, bad, warn_n = [], 0, 0
    for f in files:
        if not f.exists():
            print(f'[跳过] {f} 不存在')
            continue
        errors, warnings, info = check_pack(f)
        if strict:
            if not info['has_meta']:
                warnings = [w for w in warnings if '缺 ## 元信息 块' not in w]
                errors.append('缺 ## 元信息 块（--strict：新产物必须有）')
            up = [w for w in warnings if '无法区分预扫描包' in w]
            if up:
                warnings = [w for w in warnings if w not in up]
                errors += [w + '（--strict 下判错）' for w in up]
        rows.append((f.name, errors, warnings, info))
        if errors:
            bad += 1
        elif warnings:
            warn_n += 1

    for name, errors, warnings, info in rows:
        tag = f"{info['engine']} / {info['body_kind']}"
        if errors:
            print(f'✗ {name[:58]}')
            for i in errors:
                print(f'    [错误] {i}')
            for i in warnings:
                print(f'    [提醒] {i}')
        elif warnings:
            print(f'⚠ {name[:58]}  ({tag})')
            for i in warnings:
                print(f'    [提醒] {i}')
        else:
            print(f"✓ {name[:58]}  ({tag} / {info['body_lines']} 行带时间戳)")

    clean = len(rows) - bad - warn_n
    print(f'\n合计 {len(rows)} 份素材包：合规 {clean} / 仅提醒 {warn_n} / 错误 {bad}'
          + ('（--strict 模式）' if strict else ''))
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
