"""按 ASR 专名误识对照表扫描 Markdown，揪出"看起来没毛病"的误识。

背景：whisper / funasr-nano / B站 AI 字幕都会把专名听错。最危险的一类是
**误识结果本身是合法中文词**（如"尧舜禹"），AI 读到时不会起疑，错误写法会
一路带进正文甚至 frontmatter 的 entities，把正确名的检索入口堵死。

用法：
    # 只报告，不改动（默认）
    python check_glossary.py --dir <RAW_DIR>

    # 自动替换（仅替换语境限定为 ALL 的条目；其余需 --force 并人工确认语境）
    python check_glossary.py --dir <目录> --fix
    python check_glossary.py --dir <目录> --fix --force

    # 单文件
    python check_glossary.py --file <路径>

退出码：0 = 无命中；1 = 有命中；2 = 参数/读取错误
"""
import argparse
import re
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
DEFAULT_GLOSSARY = SKILL_DIR / 'references' / 'asr_glossary.txt'
# 术语表自身、脚本目录不扫
SKIP_PARTS = {'references', 'scripts', '.git', '.workbuddy', 'node_modules'}


def load_glossary(path):
    """返回 [(错误写法, 正确写法, 语境说明, 是否无条件替换)]"""
    text = Path(path).read_text(encoding='utf-8')
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        parts = [p.strip() for p in line.split('|')]
        if len(parts) < 2 or not parts[0] or not parts[1]:
            continue
        wrong, right = parts[0], parts[1]
        note = parts[2] if len(parts) > 2 else ''
        # 语境限定以 ALL 开头 → 无条件替换；否则需人工确认
        unconditional = note.upper().startswith('ALL')
        rows.append((wrong, right, note, unconditional))
    return rows


def is_glossary_table_row(line, wrong, right):
    """同一行里错误写法与正确写法同时出现 → 是误识对照表的记录行，属正常，跳过。

    第十二节「信息完整性」会故意保留错误写法做订正记录，不能当成污染误报。
    """
    return wrong in line and right in line


def scan_file(path, rows, fix=False, force=False):
    text = Path(path).read_text(encoding='utf-8')
    lines = text.splitlines(keepends=True)  # 保留行尾换行符，replace 不会丢
    hits = []
    changed = False
    for i, line in enumerate(lines):
        for wrong, right, note, unconditional in rows:
            if wrong not in line:
                continue
            if is_glossary_table_row(line, wrong, right):
                continue
            action = '未替换'
            if fix and (unconditional or force):
                lines[i] = lines[i].replace(wrong, right)
                action = '已替换'
                changed = True
            hits.append({
                'line': i + 1, 'wrong': wrong, 'right': right,
                'action': action, 'unconditional': unconditional,
                'text': line.strip()[:120],
            })
    if changed:
        Path(path).write_text(''.join(lines), encoding='utf-8')
    return hits, changed


def iter_md(target):
    p = Path(target)
    if p.is_file():
        yield p
        return
    # SKIP_PARTS 只用于过滤「根目录之下新出现的」scripts/ references/ 等（2026-09-16 修正）。
    # 历史行为：显式指定的根若命中 SKIP_PARTS（例如归档目录位于隐藏工作目录之下），整个目录被静默
    # 跳过并报「未发现命中」——假阴性会被误读成「没有错」。现在改为显式提醒后照常扫描。
    root_skipped = SKIP_PARTS & set(p.parts)
    if root_skipped:
        print(f'[提醒] 扫描根路径含 {sorted(root_skipped)}（递归时本会跳过，因是你显式指定 → 照常扫描）: {p}',
              file=sys.stderr)
    for f in sorted(p.rglob('*.md')):
        if SKIP_PARTS & set(f.relative_to(p).parts):
            continue
        yield f


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', default='', help='扫描目录（递归）')
    ap.add_argument('--file', default='', help='扫描单个文件')
    ap.add_argument('--glossary', default=str(DEFAULT_GLOSSARY))
    ap.add_argument('--fix', action='store_true', help='自动替换（默认只替换 ALL 语境条目）')
    ap.add_argument('--force', action='store_true', help='配合 --fix，连需人工判断语境的条目也替换')
    a = ap.parse_args()

    if not a.dir and not a.file:
        print('需要 --dir 或 --file', file=sys.stderr)
        return 2
    if not Path(a.glossary).exists():
        print(f'术语表不存在: {a.glossary}', file=sys.stderr)
        return 2

    rows = load_glossary(a.glossary)
    if not rows:
        print('术语表为空，无词条可查', file=sys.stderr)
        return 2

    total_hits = 0
    total_files = 0
    for f in iter_md(a.dir or a.file):
        hits, changed = scan_file(f, rows, a.fix, a.force)
        if not hits:
            continue
        total_files += 1
        total_hits += len(hits)
        flag = '[已修]' if changed else '[待修]'
        print(f'{flag} {f}')
        for h in hits:
            tag = 'ALL' if h['unconditional'] else '需判语境'
            print(f"    L{h['line']:<5} {h['wrong']} → {h['right']}  ({tag}, {h['action']})")
            print(f"           {h['text']}")

    print()
    if total_hits == 0:
        print(f'✓ 扫描完成，未发现术语表命中（词条 {len(rows)} 条）')
        return 0
    print(f'⚠ 命中 {total_hits} 处，涉及 {total_files} 个文件')
    if not a.fix:
        print('  仅报告未改动。确认语境后加 --fix（ALL 条目）/ --fix --force（全部）自动替换。')
    return 1


if __name__ == '__main__':
    sys.exit(main())
