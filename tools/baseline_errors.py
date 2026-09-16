# -*- coding: utf-8 -*-
"""错误基线统计：从已核对的误识对照表里，挖 ASR 错误类型分布与分层纠错命中率。

只读脚本 —— 读「纪要目录」里的 ASR 误识对照表与「素材包目录」里的转写产物，
输出统计报告，不改动任何原有文件（除 --out / --json 指定的报告）。

用途：回答三个问题 ——
  1. 这批产物的错误构成是什么（音系 / 粒度 / 来源）；
  2. 人工校对漏掉了多少（漏改率，是流程质量的客观读数）；
  3. 某个自动化纠错方案理论上能救回多少（真实收益 = 覆盖率 × 漏改率）。

依赖：pypinyin（仅用于音系维度判定）。缺失时自动降级为「不做音系分类」，
      其余维度（粒度 / 特殊类 / 分层命中）照常产出。

用法：
    python baseline_errors.py [--raw <纪要目录>] [--subs <素材包目录>]
                              [--out <报告路径>] [--json <明细路径>]

退出码：0 = 正常产出；2 = 参数/读取错误。
"""
import argparse
import glob
import json
import os
import re
import sys
from collections import Counter

DEFAULT_RAW = (os.environ.get('ASR_RAW_DIR') or os.environ.get('BILI_RAW_DIR')
               or os.path.expanduser('~/asr-notes/raw'))
DEFAULT_SUBS = (os.environ.get('ASR_SUBS_DIR') or os.environ.get('BILI_SUBS_DIR')
                or os.path.expanduser('~/asr-notes/packs'))
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_GLOSSARY = os.path.join(SKILL_DIR, 'references', 'asr_glossary.txt')

SEP = re.compile(r'^\|[\s:|-]+\|$')
LEFT_KEYS = ('原文', 'ASR', '字幕', '转写')
RIGHT_KEYS = ('应为', '正确', '还原')
CONF_KEYS = ('置信', '把握')

UNRESOLVED = ('仍存疑', '存疑', '待定', '未明', '未知', '未采信', '语义不明',
              '不可辨', '不适用', '？', '?', '未匹配', '未改', '待核', '无法核实')

PLACE_SUFFIX = ('市', '县', '镇', '区', '省', '州', '村', '岛', '山', '河', '湖',
                '湾', '港', '城', '郡', '国', '街', '路', '桥', '寺', '庙')
ORG_SUFFIX = ('公司', '集团', '品牌', '大学', '学院', '委员', '银行', '公社',
              '工厂', '政府', '部', '署', '局', '会', '党', '报', '社', '院')

ANNOT_TAIL = re.compile(r'[（(][^（）()]*[）)]?\s*$')   # 允许未闭合的注释括号
ORPHAN_CLOSE = re.compile(r'[）)]\s*$')                  # 单元格被截断后残留的孤立右括号
# 注意：不要把单独的「原」列为前缀 —— 「原数据」「原住（民）」是正常词，
# 剥掉会制造假条目（曾把「原数据 → 元数据」误当成「数据 → 元数据」）。
ANNOT_LEAD = re.compile(r'^\s*(?:应为|正确写法|疑为|疑指|即|原为)\s*[:：]?\s*')


# ---------------------------------------------------------------- 解析
def cells_of(row):
    return [c.strip() for c in row.strip().strip('|').split('|')]


def head_kind(cells):
    """识别对照表表头：第 1 列含原文类关键词，其后某列含「应为/正确/还原」。

    兼容三种已知变体：
        | 字幕原文（ASR） | 应为   | 依据 |
        | 转写原文        | 应为   | 出处 |
        | ASR 原文        | 按语境还原 | 置信度 | 说明 |
    """
    if len(cells) < 2:
        return False, None, None
    if not any(k in cells[0] for k in LEFT_KEYS):
        return False, None, None
    right_idx = None
    for i, c in enumerate(cells[1:], start=1):
        if any(k in c for k in RIGHT_KEYS):
            right_idx = i
            break
    if right_idx is None:
        return False, None, None
    conf_idx = None
    for i, c in enumerate(cells):
        if i != right_idx and any(k in c for k in CONF_KEYS):
            conf_idx = i
            break
    return True, right_idx, conf_idx


def iter_tables(text):
    lines = text.split('\n')
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        if s.startswith('|'):
            ok, ri, ci = head_kind(cells_of(s))
            if ok:
                rows, j = [], i + 1
                if j < len(lines) and SEP.match(lines[j].strip()):
                    j += 1
                while j < len(lines):
                    t = lines[j].strip()
                    if not t.startswith('|'):
                        break
                    rows.append(cells_of(t))
                    j += 1
                yield ri, ci, rows
                i = j
                continue
        i += 1


def split_cell(cell):
    out = []
    for p in re.split(r'\s*[/／]\s*', cell.strip()):
        p = p.strip()
        if not p:
            continue
        out.extend([q.strip() for q in re.split(r'、', p) if q.strip()])
    return out


def parse_file(path):
    """返回 (pairs, unresolved_count)。pairs: [(错, 对, 置信, 依据)]"""
    t = open(path, encoding='utf-8', errors='ignore').read()
    pairs, unresolved = [], 0
    for ri, ci, rows in iter_tables(t):
        for cells in rows:
            if len(cells) <= ri:
                continue
            wrong_raw, right_raw = cells[0], cells[ri]
            conf = cells[ci] if (ci is not None and ci < len(cells)) else ''
            note = ' | '.join(c for k, c in enumerate(cells) if k not in (0, ri, ci))
            if not wrong_raw or not right_raw:
                continue
            if any(u in right_raw for u in UNRESOLVED):
                unresolved += 1
                continue
            if '→' in wrong_raw or '->' in wrong_raw:
                for seg in re.split(r'[、,，]', wrong_raw):
                    if '→' in seg or '->' in seg:
                        a, b = re.split(r'→|->', seg, maxsplit=1)
                        pairs.append((a.strip(), b.strip(), conf, note))
                continue
            ws, rs = split_cell(wrong_raw), split_cell(right_raw)
            if not ws or not rs:
                continue
            if len(ws) == len(rs):
                for a, b in zip(ws, rs):
                    pairs.append((a, b, conf, note))
            else:
                for a in ws:
                    pairs.append((a, '/'.join(rs), conf, note))
    return pairs, unresolved


def engine_of(text):
    """判定素材来源：平台自动字幕 / 本地 ASR / 两者混用。"""
    has_local = any(k in text for k in ('本地 Whisper', '本地 ASR', 'Whisper large'))
    has_platform_ai = any(k in text for k in ('AI 生成中文字幕', 'B站 AI', 'B 站 AI',
                                              '官方 AI 字幕'))
    if has_local and has_platform_ai:
        return 'mixed'
    if has_local:
        return 'local-asr'
    if has_platform_ai:
        return 'platform-ai'
    return 'unknown'


# ---------------------------------------------------------------- 音系
def has_pypinyin():
    try:
        import pypinyin          # noqa: F401
        return True
    except ImportError:
        return False


def syllables(word):
    """拼音音节（不含声调）。无 pypinyin 时返回空列表 → 音系判为「未判定」。"""
    try:
        import pypinyin
    except ImportError:
        return []
    out = []
    for ch in word:
        if '\u4e00' <= ch <= '\u9fff':
            py = pypinyin.lazy_pinyin(ch, style=pypinyin.Style.NORMAL)
            out.append(py[0] if py else '')
        elif ch.isalpha():
            out.append(ch.lower())
    return [s for s in out if s]


def onset_rime(syl):
    for on in ('zh', 'ch', 'sh', 'ng'):
        if syl.startswith(on):
            return on, syl[len(on):]
    if syl and syl[0] in 'bpmfdtnlgkhjqxrzcsyw':
        return syl[0], syl[1:]
    return '', syl


def lev(a, b):
    if abs(len(a) - len(b)) > 2:
        return 9
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def phon_class(wrong, right):
    """返回 (类别, 细节)。类别：同音 / 近音 / 非音系 / 未判定。"""
    wa, rb = syllables(wrong), syllables(right)
    if not wa or not rb:
        return '未判定', 'no-syllables'
    if wa == rb:
        return '同音', 'syllables-identical'
    if len(wa) == len(rb):
        same = sum(1 for x, y in zip(wa, rb) if x == y)
        if same == len(wa):
            return '同音', 'all-syllables-identical'
        if same >= max(1, len(wa) - 1):
            diffs = [(x, y) for x, y in zip(wa, rb) if x != y]
            if all(onset_rime(x)[0] == onset_rime(y)[0] or
                   onset_rime(x)[1] == onset_rime(y)[1] for x, y in diffs):
                return '近音', 'one-syllable-near'
            if all(lev(x, y) <= 1 for x, y in diffs):
                return '近音', 'one-syllable-edit1'
            return '非音系', 'one-syllable-far'
        ons_w = [onset_rime(x)[0] for x in wa]
        ons_r = [onset_rime(y)[0] for y in rb]
        if ons_w == ons_r:
            return '近音', 'onsets-identical'
        if lev(''.join(wa), ''.join(rb)) <= max(1, len(''.join(wa)) // 3):
            return '近音', 'global-edit-small'
        return '非音系', 'many-syllables-differ'
    n = min(len(wa), len(rb))
    same = sum(1 for x, y in zip(wa[:n], rb[:n]) if x == y)
    if same >= max(1, n - 1) and abs(len(wa) - len(rb)) <= 1:
        return '近音', 'len-mismatch-close'
    if same == 0:
        return '非音系', 'no-common-syllable'
    return '非音系', 'len-mismatch-far'


def strip_annot(s):
    """剥离括号注解与前缀词，避免注解污染音系比对。"""
    s = (s or '').strip()
    for _ in range(3):
        s2 = ANNOT_TAIL.sub('', s).strip()
        if s2 == s:
            break
        s = s2
    s = ORPHAN_CLOSE.sub('', s).strip()
    return ANNOT_LEAD.sub('', s).strip(' 　\u3000')


def best_phon(wrong, right_raw):
    """多候选取最接近的音系类别。"""
    cands = [c for c in split_cell(right_raw) if c] or [right_raw]
    order = {'同音': 3, '近音': 2, '非音系': 1, '未判定': 0}
    best, best_detail = None, ''
    for c in cands:
        cls, det = phon_class(strip_annot(wrong), strip_annot(c))
        if best is None or order[cls] > order[best]:
            best, best_detail = cls, det
        if best == '同音':
            break
    return best or '未判定', best_detail


# ---------------------------------------------------------------- 判据
def load_lexicon(path):
    wrong_set, right_set, entries = set(), set(), []
    if not os.path.exists(path):
        return wrong_set, right_set, entries
    for line in open(path, encoding='utf-8').read().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        parts = [p.strip() for p in line.split('|')]
        if len(parts) < 2 or not parts[0] or not parts[1]:
            continue
        wrong_set.add(parts[0])
        right_set.add(parts[1])
        entries.append(tuple(parts[:3]))
    return wrong_set, right_set, entries


def semantic_class(right, note, pack):
    """粗判对词语义类别：强证据 = 出现在素材包头部（标题/章节）；弱证据 = 词形/依据。"""
    key = right.split('/')[0]
    if pack and key and len(key) >= 2 and key in pack[:2500]:
        return '专名-标题证据'
    if note and any(k in note for k in ('地名', '人名', '镇名', '县名', '市名',
                                        '品牌', '公司', '机构', '人物', '城市',
                                        '所在', '产地', '遗址', '创始人')):
        return '专名-依据证据'
    if key and (key.endswith(PLACE_SUFFIX) or key.endswith(ORG_SUFFIX)):
        return '专名-词形证据'
    if key and re.match(r'^[A-Za-z]', key):
        return '专名-英文形'
    return '未判定'


def granularity(wrong, right):
    n = max(len(wrong), len(right))
    if n <= 4:
        return '词级'
    if n <= 10:
        return '短语级'
    return '句级'


def special_class(wrong, right, note):
    """可程序化识别的特殊错误类（与音系维度正交）。"""
    tags = []
    both = wrong + right
    if re.search(r'\d', both):
        tags.append('数字/编号')
    if re.search(r'[零一二三四五六七八九十百千万亿]{2,}(?:年|岁|度|倍|万|亿|%|个|人|元)',
                 both):
        tags.append('数量表述')
    if any(k in (note or '') for k in ('断句', '分词', '语序', '拆句', '粘连')):
        tags.append('结构/断句')
    if re.search(r'[A-Za-z]{2,}', both):
        tags.append('含拉丁字')
    return tags


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--raw', default=DEFAULT_RAW)
    ap.add_argument('--subs', default=DEFAULT_SUBS)
    ap.add_argument('--glossary', default=DEFAULT_GLOSSARY)
    ap.add_argument('--out', default='')
    ap.add_argument('--json', default='')
    a = ap.parse_args()

    if not os.path.isdir(a.raw):
        print('raw 目录不存在: %s' % a.raw, file=sys.stderr)
        return 2

    files = [f for f in sorted(glob.glob(os.path.join(a.raw, '*.md')))
             if '误识对照' in open(f, encoding='utf-8', errors='ignore').read()]
    pack_index = {}
    for p in glob.glob(os.path.join(a.subs, '*.md')):
        m = re.search(r'BV[0-9A-Za-z]{10}', os.path.basename(p))
        if m:
            pack_index.setdefault(m.group(0), p)

    lex_wrong, lex_right, lex_entries = load_lexicon(a.glossary)
    use_py = has_pypinyin()

    rows, unresolved_total, no_pack = [], 0, 0
    for f in files:
        t = open(f, encoding='utf-8', errors='ignore').read()
        pairs, unres = parse_file(f)
        unresolved_total += unres
        eng = engine_of(t)
        m = re.search(r'BV[0-9A-Za-z]{10}', t)
        pack = None
        if m and m.group(0) in pack_index:
            pack = open(pack_index[m.group(0)], encoding='utf-8', errors='ignore').read()
        else:
            no_pack += 1

        for wrong, right, conf, note in pairs:
            w_clean, r_clean = strip_annot(wrong), strip_annot(right)
            # 清洗后两侧相同（或为空）→ 该行不是「误识对照」，跳过
            if not w_clean or not r_clean or w_clean == r_clean:
                continue
            cls, detail = best_phon(wrong, right)
            key = r_clean.split('/')[0]
            l1a = (w_clean in lex_wrong) or (key in lex_right) or (wrong in lex_wrong)
            l1b = cls in ('同音', '近音')
            l2w = l2s = False
            l2cnt = l2wcnt = 0
            if pack:
                body = '\n'.join(l for l in pack.split('\n')
                                 if not l.strip().startswith('|'))
                l2cnt = body.count(key) if key else 0
                l2wcnt = body.count(w_clean) if w_clean else 0
                l2w = l2cnt >= 1
                l2s = l2cnt >= 1 and l2cnt > l2wcnt
            rows.append({
                'file': os.path.basename(f), 'engine': eng, 'conf': conf,
                'wrong': wrong, 'right': right, 'note': note,
                'w_clean': w_clean, 'r_clean': r_clean,
                'phon': cls, 'phon_detail': detail,
                'gran': granularity(w_clean, r_clean),
                'sem': semantic_class(r_clean, note, pack),
                'l1_lex': l1a, 'l1_phon': l1b,
                'l2_consistency': l2w, 'l2_strict': l2s,
                'l2_count': l2cnt, 'l2_wrong_count': l2wcnt,
                'special': special_class(w_clean, r_clean, note),
            })

    n = len(rows)
    if n == 0:
        print('未解析到任何错误对，请检查 --raw 路径', file=sys.stderr)
        return 2

    out = []
    out.append('== 错误基线（自动打标）==')
    out.append('样本：%d 份含误识对照的纪要 / %d 个有效错误对'
               % (len(files), n))
    out.append('      （排除无法判定 %d 条；未配到素材包 %d 份；'
               '音系维度 %s）' % (unresolved_total, no_pack,
                                '启用 pypinyin' if use_py else '未启用（pypinyin 缺失）'))
    out.append('')
    out.append('-- 音系维度 --')
    for k, v in Counter(r['phon'] for r in rows).most_common():
        out.append('   %-10s %4d  %5.1f%%' % (k, v, 100.0 * v / n))
    out.append('')
    out.append('-- 粒度维度 --')
    for k, v in Counter(r['gran'] for r in rows).most_common():
        out.append('   %-10s %4d  %5.1f%%' % (k, v, 100.0 * v / n))
    out.append('')
    out.append('-- 语义类别（自动启发）--')
    for k, v in Counter(r['sem'] for r in rows).most_common():
        out.append('   %-16s %4d  %5.1f%%' % (k, v, 100.0 * v / n))
    out.append('')
    out.append('-- 分层纠错理论命中率 --')
    hit_lex = sum(1 for r in rows if r['l1_lex'])
    hit_phon = sum(1 for r in rows if r['l1_phon'])
    lexphon_both = sum(1 for r in rows if r['l1_lex'] and r['l1_phon'])
    l1any = hit_lex + hit_phon - lexphon_both          # |层①|
    hit_l2 = sum(1 for r in rows if r['l2_consistency'])
    hit_l2s = sum(1 for r in rows if r['l2_strict'])
    both = sum(1 for r in rows if r['l2_consistency'] and
               (r['l1_lex'] or r['l1_phon']))            # |层①∩层②|
    both_s = sum(1 for r in rows if r['l2_strict'] and
                 (r['l1_lex'] or r['l1_phon']))
    out.append('   层①a 词典直命中            %4d  %5.1f%%   （词典现有 %d 条）'
               % (hit_lex, 100.0 * hit_lex / n, len(lex_entries)))
    out.append('   层①b 音系可召回（上限口径） %4d  %5.1f%%   ← 同音+近音'
               % (hit_phon, 100.0 * hit_phon / n))
    out.append('   层① 合计（上限口径）       %4d  %5.1f%%'
               % (l1any, 100.0 * l1any / n))
    out.append('   层② 片内一致性（弱）        %4d  %5.1f%%   ← 对词出现 ≥1 次'
               % (hit_l2, 100.0 * hit_l2 / n))
    out.append('   层② 片内一致性（强）        %4d  %5.1f%%   ← 对词出现次数 > 错词'
               % (hit_l2s, 100.0 * hit_l2s / n))
    out.append('   层①∪层②（弱口径，上限）   %4d  %5.1f%%'
               % (l1any + hit_l2 - both,
                  100.0 * (l1any + hit_l2 - both) / n))
    out.append('   层①∪层②（强口径，上限）   %4d  %5.1f%%'
               % (l1any + hit_l2s - both_s,
                  100.0 * (l1any + hit_l2s - both_s) / n))
    out.append('')
    out.append('   注：层①b 是上限。同音/近音只说明拼音匹配"能生成候选"；')
    out.append('       真正选中正确写法还要求它已在词典/候选表内。')
    out.append('       本机词典现 %d 条且与样本零重叠 → 层①实际可达值 ≈ 0%%。'
               % len(lex_entries))
    out.append('')
    out.append('-- 特殊类（与音系维度正交，可程序化识别）--')
    sp = Counter()
    for r in rows:
        for t in r['special']:
            sp[t] += 1
    for k, v in sp.most_common():
        out.append('   %-12s %4d  %5.1f%%' % (k, v, 100.0 * v / n))
    out.append('   任一特殊类   %4d  %5.1f%%'
               % (sum(1 for r in rows if r['special']),
                  100.0 * sum(1 for r in rows if r['special']) / n))
    out.append('')
    out.append('-- 按来源引擎 × 音系 --')
    for eng in ('platform-ai', 'local-asr', 'mixed', 'unknown'):
        sub = [r for r in rows if r['engine'] == eng]
        if not sub:
            continue
        c = Counter(r['phon'] for r in sub)
        tot = len(sub)
        out.append('   %-10s n=%3d  同音 %5.1f%%  近音 %5.1f%%  非音系 %5.1f%%'
                   % (eng, tot, 100.0 * c['同音'] / tot,
                      100.0 * c['近音'] / tot, 100.0 * c['非音系'] / tot))
    out.append('')
    out.append('-- 交叉：音系 × 粒度 --')
    for ph in ('同音', '近音', '非音系'):
        sub = [r for r in rows if r['phon'] == ph]
        if not sub:
            continue
        c = Counter(r['gran'] for r in sub)
        out.append('   %-6s n=%3d  词级 %3d  短语级 %3d  句级 %3d'
                   % (ph, len(sub), c['词级'], c['短语级'], c['句级']))
    out.append('')
    import random
    rnd = random.Random(20260916)
    out.append('== 人工抽检样本（seed=20260916，按音系分层，每类最多 12 条）==')
    out.append('   核对要点：音系判定是否符合直觉；长条目是否为真正的句级音误')
    for ph in ('同音', '近音', '非音系'):
        sub = [r for r in rows if r['phon'] == ph]
        rnd.shuffle(sub)
        out.append('')
        out.append('-- %s（n=%d）--' % (ph, len(sub)))
        for r in sub[:12]:
            out.append('   %-13s -> %-24s | %s | %s'
                       % (r['wrong'][:13], r['right'][:24],
                          r['phon_detail'], r['gran']))

    dest = a.out or os.path.join(os.getcwd(),
                                 'baseline_errors_report.txt')
    open(dest, 'w', encoding='utf-8').write('\n'.join(out))
    if a.json:
        json.dump(rows, open(a.json, 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=1)
    print('错误对 %d 个；报告 -> %s' % (n, dest))
    print('\n'.join(out[:32]))
    return 0


if __name__ == '__main__':
    sys.exit(main())
