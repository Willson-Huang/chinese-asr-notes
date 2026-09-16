# 播客转写素材包（本地 ASR 转写）

## 基本信息
- 标题：示例技术播客 第 12 期 —— 语音识别工程实践
- 发布：2026-09-01 ｜ 时长：03:00
- 链接：https://example.com/podcast/ep12

## 元信息
> 机器可读的产出信息；生成纪要与增强条目时不要抄进正文。
- route: asr:whisper:large-v3-turbo@cuda
- engine: whisper
- model: large-v3-turbo
- device: cuda
- source: 本地文件（ep12.m4a）
- ts_granularity: segment
- hotwords: disabled
- audio_sec: 180.0

## 转写全文（本地 whisper large-v3-turbo · cuda）
> 标注 `[广告?]` 的行为疑似带货口播，生成纪要时跳过，不写入结论。

[00:00:01] 这是第一行正文，用来验证素材包校验能否通过。
[00:00:06] 这是第二行正文，同样带 [hh:mm:ss] 时间戳前缀。
[00:00:12] 第三行，正文行必须全部带前缀，缺一行即判格式损坏。
[00:00:19] 这段示例用于演示：本工具链同样适用于播客、会议录音、课程录像。
