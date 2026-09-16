#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Fun-ASR-Nano 独立 adapter（由 asr_eval 解释器运行，不污染 whisper venv）。

职责：把音频转写成与 faster-whisper 一致的 segments 结构，供上层脚本归一写入素材包。

用法:
  <asr_eval python> funasr_adapter.py --audio <wav/m4a> [--language 中文]
                                      [--hotwords-file <txt>] [--out <json路径>]
                                      [--max-hotwords 80] [--quiet]

输出 JSON:
{
  "ok": true,
  "engine": "funasr-nano",
  "model": "FunAudioLLM/Fun-ASR-Nano-2512",
  "segments": [{"start": 240.0, "end": 247.2, "text": "..."}, ...],
  "load_sec": 49.9, "asr_sec": 9.5,
  "hotwords_count": 20, "hotwords_sha256": "..." 或 null,
  "hotwords_truncated": false, "error": null
}

热词约束（2026-09-06 评估定稿）：
- 默认每视频 20-50 词，硬上限 80（过长会注意力稀释/误插词）
- 去重 + 去空行，超上限截断并标记 hotwords_truncated
- 热词会随每个 VAD 分段重复注入，不是一次性成本，故必须限制条数
"""
import argparse
import hashlib
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

MODEL_ID = 'FunAudioLLM/Fun-ASR-Nano-2512'
DEFAULT_MAX_HOTWORDS = 80
# VAD 分段并发解码数。默认 1 —— 保留已知良好行为。
# 实测（NVIDIA RTX 4060 Ti 8GB，180s 与 24:43 全长音频）：1/4/8/16 的实时倍率
# 分别是 5.07/5.01/5.06/5.04x，**并发解码对本卡零收益**（GPU 由单进程即吃满，
# 双进程并发实测加速比仅 1.04x）。且 batch=4/8 时个别词与 batch=1 不一致（解码
# 非确定性），batch=16 才完全一致——用参数换不来速度却可能换掉稳定性，故默认 1。
# 该参数保留是给未来换更大显存的卡预留调优入口。
DEFAULT_INFER_BATCH = 1


# --------------------------------------------------------------------------- #
# 进度上报（可选）
# 由环境变量 ASR_PROGRESS_DIR（或历史名 BILI_PROGRESS_DIR）启用；未设置时全部为空操作。
# 任何异常一律吞掉 —— 进度是旁路，绝不能让看板拖垮转写。
# Nano 一次 generate 处理整条音频、没有细粒度回调，故按
# 「已用时间 / (音频时长 ÷ 实测倍率 5.08)」插值出百分比。
# --------------------------------------------------------------------------- #
def _hub():
    if not (os.environ.get('ASR_PROGRESS_DIR') or os.environ.get('BILI_PROGRESS_DIR')):
        return None
    try:
        me = str(Path(__file__).resolve().parent)
        if me not in sys.path:
            sys.path.insert(0, me)
        import progress_hub
        return progress_hub
    except Exception:
        return None


def _dur_sec(wav):
    """读 wav 时长（秒）。失败返回 None。"""
    try:
        import wave
        with wave.open(str(wav)) as w:
            return round(w.getnframes() / float(w.getframerate() or 1), 1)
    except Exception:
        return None


class _AsrReporter:
    """单条音频的转写进度上报器：起始 + 定时插值 + 结束。"""

    INTERVAL = 3.0

    def __init__(self, key, wav, label=None):
        self.h = _hub()
        self.task = None
        self.dur = None
        self.t0 = None
        self._stop = threading.Event()
        self._th = None
        if not self.h:
            return
        try:
            self.task = self.h.task_of(key)
            self.dur = _dur_sec(wav)
            self.t0 = time.time()
            self.h.auto(task=self.task, stage='asr', pct=1.0, duration_sec=self.dur,
                        started=self.t0, note=(label or '转写中'))
            self._th = threading.Thread(target=self._loop, daemon=True)
            self._th.start()
        except Exception:
            self.h = None

    def _loop(self):
        while not self._stop.wait(self.INTERVAL):
            try:
                el = time.time() - self.t0
                pct = self.h.pct_from_elapsed(el, self.dur)
                self.h.auto(task=self.task, stage='asr', pct=pct, elapsed=round(el, 1),
                            note='转写中 %s%%' % (('%.0f' % pct) if pct is not None else '?'))
            except Exception:
                pass

    def done(self, asr_sec=None, note='转写完成'):
        try:
            self._stop.set()
            if self.h:
                self.h.auto(task=self.task, stage='done', pct=100.0,
                            elapsed=round(asr_sec, 1) if asr_sec else None, note=note)
        except Exception:
            pass

    def fail(self, why):
        try:
            self._stop.set()
            if self.h:
                self.h.auto(task=self.task, stage='fail', note=str(why)[:120])
        except Exception:
            pass


def load_hotwords(path, limit):
    """读热词文件，并区分未传、缺失、为空与实际加载的状态。"""
    if not path:
        return [], None, None, False, 0, 'disabled'
    p = Path(path)
    if not p.is_file():
        return [], None, None, False, 0, 'missing'
    raw = p.read_text(encoding='utf-8', errors='replace')
    source_sha = hashlib.sha256(raw.encode('utf-8')).hexdigest()
    words, seen = [], set()
    for line in raw.splitlines():
        w = line.strip()
        if w and not w.startswith('#') and w not in seen:
            seen.add(w)
            words.append(w)
    total = len(words)
    truncated = total > limit
    applied = words[:limit]
    applied_sha = hashlib.sha256('\n'.join(applied).encode('utf-8')).hexdigest() if applied else None
    return applied, applied_sha, source_sha, truncated, total, ('loaded' if applied else 'empty')


SENT_END = '。！？；'


def _ascii_alnum(ch):
    return bool(ch) and (ch.isascii() and ch.isalnum())


def group_tokens(r0, gap=0.3, max_chars=60):
    """把 token 级 timestamps 聚合成句子级段落。

    Fun-ASR-Nano 不返回 sentence_info；timestamps 是 token 级且单位=秒，
    对应 LLM 正确分支（ctc_text 是另一条较弱的 CTC 分支，不要用）。
    断句规则：句末标点优先，其次静音间隔 >gap，再其次长度上限。
    """
    toks = r0.get('timestamps') or []
    if not toks:
        return []
    out, cur = [], None
    for t in toks:
        if not isinstance(t, dict):
            continue
        tok = (t.get('token') or '').strip()
        if not tok:
            continue
        st = t.get('start_time')
        en = t.get('end_time')
        st = float(st) if isinstance(st, (int, float)) else 0.0
        en = float(en) if isinstance(en, (int, float)) else st
        if cur is None:
            cur = {'start': st, 'end': en, 'text': tok}
            continue
        need_break = (
            (cur['text'] and cur['text'][-1] in SENT_END)
            or (st - cur['end'] > gap)
            or (len(cur['text']) + len(tok) > max_chars)
        )
        prev = cur['text'][-1] if cur['text'] else ''
        if need_break:
            out.append(cur)
            cur = {'start': st, 'end': en, 'text': tok}
        elif prev and _ascii_alnum(prev) and _ascii_alnum(tok[:1] or ''):
            # 英文/数字 token 之间补空格，避免拼成 "Therearenostrangerstolove"
            cur['text'] += ' ' + tok
            cur['end'] = en
        else:
            cur['text'] += tok
            cur['end'] = en
    if cur:
        out.append(cur)

    # 清理：纯标点段（如孤立的「，」）不应占一行时间戳，并入相邻段
    merged = []
    for b in out:
        if merged and not re.sub(r'[\s，。！？；、,.!?;:：""\'\'（）()【】\[\]—\-…]', '', b['text']):
            merged[-1]['text'] += b['text']
            merged[-1]['end'] = b['end']
        else:
            merged.append(dict(b))
    # 首段若是纯标点则并到第二段
    if len(merged) > 1 and not re.sub(r'[\s，。！？；、,.!?;:：""\'\'（）()【】\[\]—\-…]', '', merged[0]['text']):
        merged[1]['text'] = merged[0]['text'] + merged[1]['text']
        merged[1]['start'] = merged[0]['start']
        merged.pop(0)

    # 过滤纯事件标签段（[noise]/[SP K]/[BGM] 等），避免污染素材包正文
    kept = [b for b in merged
            if re.sub(r'\[[^\]]{1,12}\]', '', b['text']).strip()]

    return [{'start': round(b['start'], 2), 'end': round(b['end'], 2), 'text': b['text']}
            for b in (kept or merged)]


def ensure_wav(path):
    """非 wav 音频统一转 16k 单声道 wav（torchaudio 无 ffmpeg 后端解不了 m4a）。"""
    p = Path(path)
    if p.suffix.lower() == '.wav':
        return str(p)
    try:
        import imageio_ffmpeg
        import subprocess as _sp
        ff = imageio_ffmpeg.get_ffmpeg_exe()
        out = p.with_suffix('.nano16k.wav')
        r = _sp.run([ff, '-y', '-i', str(p), '-ar', '16000', '-ac', '1', '-vn', str(out)],
                    capture_output=True, text=True)
        if r.returncode == 0 and out.is_file():
            return str(out)
    except Exception:
        pass
    return str(p)  # 转码失败则原样交给 funasr（可能报错，由上层捕获）


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--audio', default=None, help='单条音频路径')
    ap.add_argument('--batch-json', default=None,
                    help='批量：JSON 数组文件路径 [{"key":..., "audio":...}, ...]；'
                         '一次加载模型处理全部，避免每条重复 50-70s 冷启动')
    ap.add_argument('--out', default=None, help='输出 JSON 路径；不传则打 stdout')
    ap.add_argument('--language', default='auto',
                    help='auto(默认，自动检测) / 中文 / English；'
                         '强制指定语言会导致外语内容(如英文歌)被硬转成中文错字')
    ap.add_argument('--hotwords-file', default=None)
    ap.add_argument('--max-hotwords', type=int, default=DEFAULT_MAX_HOTWORDS)
    ap.add_argument('--infer-batch-size', type=int, default=DEFAULT_INFER_BATCH,
                    help='VAD 分段并发解码数（默认 %d）。=1 时每段单独解码，'
                         '长音频下 GPU 利用率极低、实时倍率掉到 2x；调大可显著提速' % DEFAULT_INFER_BATCH)
    ap.add_argument('--quiet', action='store_true')
    a = ap.parse_args()

    if not a.audio and not a.batch_json:
        print(json.dumps({'ok': False, 'error': '需要 --audio 或 --batch-json'}, ensure_ascii=False))
        sys.exit(1)

    result = {
        'ok': False, 'engine': 'funasr-nano', 'model': MODEL_ID,
        'segments': [], 'load_sec': None, 'asr_sec': None,
        'hotwords_count': 0, 'hotwords_sha256': None, 'hotwords_source_sha256': None,
        'hotwords_truncated': False, 'hotwords_total': 0, 'hotwords_status': 'disabled',
        'error': None,
    }
    try:
        if a.max_hotwords < 1:
            raise ValueError('--max-hotwords 必须大于 0')
        hotwords, hw_sha, hw_source_sha, hw_trunc, hw_total, hw_status = load_hotwords(
            a.hotwords_file, a.max_hotwords)
        result.update({'hotwords_count': len(hotwords), 'hotwords_sha256': hw_sha,
                       'hotwords_source_sha256': hw_source_sha,
                       'hotwords_truncated': hw_trunc, 'hotwords_total': hw_total,
                       'hotwords_status': hw_status})
        # 显式传了热词文件却读不到 = 静默降级成零热词，必须出声，否则会误以为已启用专名增强
        if hw_status == 'missing':
            print(f'[warn] 热词文件不存在，已按零热词转写: {a.hotwords_file}', file=sys.stderr)
        elif hw_status == 'empty':
            print(f'[warn] 热词文件无有效词条，已按零热词转写: {a.hotwords_file}', file=sys.stderr)

        os.environ.setdefault('HF_ENDPOINT', 'https://hf-mirror.com')
        # 预导入触发 SenseVoiceEncoderSmall 注册（funasr 内置表未自动注册）
        import funasr.models.sense_voice.model  # noqa: F401
        from funasr import AutoModel

        t0 = time.time()
        model = AutoModel(
            model=MODEL_ID, device='cuda:0', hub='ms', disable_update=True,
            vad_model='fsmn-vad', vad_kwargs={'max_single_segment_time': 60000},
        )
        load_sec = round(time.time() - t0, 1)
        result['load_sec'] = load_sec

        t1 = time.time()
        # 统一按批量处理（单条视为 1 条批量）：一次加载模型，逐条 generate
        items = ([{'key': 'single', 'audio': a.audio}] if a.audio
                 else json.loads(Path(a.batch_json).read_text(encoding='utf-8')))
        per_item = []
        for it in items:
            wav = ensure_wav(it['audio'])
            converted = Path(wav) != Path(it['audio'])
            t_item = time.time()
            rep = _AsrReporter(it.get('key'), wav)
            try:
                gen_kw = dict(input=[wav], cache={}, batch_size=a.infer_batch_size,
                              language=a.language, itn=True)
                # hotwords 为空时不能传 None：funasr 内部会对 None 取 len() 而崩溃
                if hotwords:
                    gen_kw['hotwords'] = hotwords
                res = model.generate(**gen_kw)
                r0 = (res or [{}])[0]
                segs = group_tokens(r0)
                # 区分「确实静音」与「时间戳解析异常」：后者不能伪装成成功进知识库
                if segs:
                    ts_status = 'ok'
                else:
                    text = (r0.get('text') or '').strip()
                    if text:
                        # 有正文却拿不到 token 时间戳 = 解析异常，交给上层判失败
                        segs = [{'start': 0.0, 'end': 0.0, 'text': text}]
                        ts_status = 'no_ts'
                    else:
                        ts_status = 'empty'
                asr_sec = round(time.time() - t_item, 2)
                rep.done(asr_sec, note='转写完成 %ss · %.1fx' % (
                    asr_sec, (rep.dur / asr_sec) if (rep.dur and asr_sec) else 0))
                per_item.append({'key': it.get('key'), 'audio': it['audio'], 'segments': segs,
                                 'asr_sec': asr_sec,
                                 'timestamp_status': ts_status,
                                 'audio_sec': (segs[-1]['end'] if segs else 0.0)})
            except Exception as _e:
                rep.fail('%s: %s' % (type(_e).__name__, _e))
                raise
            finally:
                # m4a 的 16k wav 是 adapter 内部临时产物，绝不留在共享音频目录。
                if converted:
                    try:
                        Path(wav).unlink(missing_ok=True)
                    except Exception:
                        pass
        result['asr_sec'] = round(time.time() - t1, 1)
        result['items'] = per_item
        # 单条模式保留顶层 segments/timestamp_status，兼容原有调用方
        result['segments'] = per_item[0]['segments'] if per_item else []
        result['timestamp_status'] = (per_item[0]['timestamp_status']
                                      if per_item else 'empty')
        result['ok'] = True
    except Exception as e:
        result['error'] = f'{type(e).__name__}: {e}'

    payload = json.dumps(result, ensure_ascii=False)
    if a.out:
        Path(a.out).write_text(payload, encoding='utf-8')
    if not a.quiet:
        print(payload)
    sys.exit(0 if result['ok'] else 1)


if __name__ == '__main__':
    main()
