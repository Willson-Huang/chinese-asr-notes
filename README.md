# chinese-asr-notes

中文音频转文字的实战笔记与工具链。内容包括引擎怎么选、专名为什么会被听错，以及哪些自动化纠错路线已经用真实数据否决掉了。

> Chinese ASR field notes: which engine to pick, why proper nouns come out wrong, and which "automated correction" ideas have already been ruled out with hard numbers. Platform-agnostic — works with any Chinese audio: podcasts, meetings, lectures, interviews, or Bilibili videos.
>
> 与平台无关。播客、会议录音、课程录像、访谈、B站视频，处理逻辑是同一套，不做 B站 也能直接用。

---

## 内容范围

| 包含 | 不包含 |
|---|---|
| 实测经验与数据：同一段音频上 whisper / Fun-ASR-Nano / Qwen3-ASR 的专名正确率对照 | 又一个大而全的语音识别框架 |
| 可复现的评测方法：怎么量化错误构成、人工漏改率、某个纠错方案究竟能救回多少 | 模型训练与微调 |
| 能直接运行的工具：4 个零第三方依赖的脚本，加 2 个引擎适配脚本 | 云服务绑定，也不需要申请 API Key |
| 一份「别再重跑」的清单：已被真实数据否决的技术路线，附现象与数字 | 现成的转写服务 |

**谁适合看**：要把中文音频（播客 / 会议 / 课程 / 访谈）转成可检索文字的人；正在挑 ASR 引擎的人；想给转写结果加一层自动纠错、但不确定值不值得投入的人。

---

## 核心结论

### 引擎选择直接决定专名对错

同一段 60 秒中文样本、同一台机器：

| 模型 | 专名正确率 | 典型表现 |
|---|---|---|
| `whisper-large-v3-turbo` | 2/11 | 肇庆 → 赵庆、怀集 → 淮吉、利玛窦 → 立马逗 |
| **`Fun-ASR-Nano-2512`（800M）** | 10/11 | 上述专名全对，仅一处近似错 |
| `Qwen3-ASR-1.7B` | 7/11 | 肇庆 → 赵庆（与 whisper 同错），参数量还更大 |

代价是不对称的：whisper 快约 4 倍（19.2x 对 5.1x 实时），但专名一旦写错，会一路带进正文与索引，半年后搜不到。专名密集的内容用 Nano，等于拿时间换可检索性。

> ⚠️ whisper 的错可能是高置信度错，判断质量时不要只看置信度。两个引擎结果不一致时，以 Nano 为准。

### 「合法中文词」型误识最危险

最危险的一类误识，是错写本身仍然是合法中文词。`姚顺雨`（腾讯首席 AI 科学家）被反复误识成 `尧舜禹`，三个古代圣王，同音，AI 读到不会起疑，错误会一路进正文、甚至进元数据字段，把正确名字的检索入口堵住。

人工漏改率实测 20.8%（787 对已核对样本），分布很有规律：越像正常中文的错误越容易漏改。

| 错误类型 | 漏改率 |
|---|---|
| 非音系（语义漂移） | 29.2% |
| 近音 | 20.6% |
| 同音 | 13.3% |

### 校对按类型查，比查词表有效

同一份素材包、同一个校对者：词表法 0 个真阳性，按类型核查 3 个真错写。

「校对三查」的核查顺序是 企业 / 品牌名 → 地名 → 历史地名，逐类抽行核对。详见 [docs/校对方法.md](docs/校对方法.md)。

### 一条通用口径

> 纠错层的真实价值 = 覆盖率 × 漏改率

只算覆盖率，会把「人工已经改对的」也算成收益。做任何「自动化替代人工」的收益评估都该这么算。这条口径修正是本项目最有价值的产出，它直接把四个看起来很有前景的纠错方案算到了 2%–4%，不值得投入。

---

## 快速开始

```bash
git clone https://github.com/Willson-Huang/chinese-asr-notes.git
cd chinese-asr-notes

# 4 个脚本零第三方依赖，克隆下来就能跑
python tools/verify_pack.py examples/sample-podcast-pack.md     # 校验一份素材包
python tools/verify_structure.py --dir <你的纪要目录>            # 校验结构化条目
python tools/check_glossary.py --dir <目录>                     # 扫专名误识
python tools/baseline_errors.py --raw <纪要目录> --subs <素材包目录>   # 错误基线统计
```

需要外部依赖的两个脚本：

```bash
# 错误统计的音系维度需要 pypinyin（缺失会自动降级，其余维度照常产出）
pip install pypinyin

# Fun-ASR-Nano 引擎适配 —— 依赖 funasr，且与 faster-whisper 依赖冲突
# 请单独建一个 venv，不要和 whisper 装在一起
pip install --no-deps funasr && pip install numpy imageio-ffmpeg
python tools/funasr_adapter.py --audio <音频文件> --out out.json
```

---

## 已实测否决的路线（别再重跑）

> 这一节最省时间。每条都有实测数据与具体样本，反复重试只会重复付出同样的成本。

| 方向 | 实测结果 | 结论 |
|---|---|---|
| 汇总标题/简介里的专名做候选表 | 对 782 个真实错误，并集覆盖 **5.0%** | 不做 |
| 确定性规则层（拉丁字/数字规范化） | 涉及拉丁字或数字的错误里 **89.6% 是声学识别错误**（TCL → 「太刺」、ABB → 「APP」），规则无解；真实可修 **2.1%** | 不做 |
| 批量修空格粘连 | 素材包 **338 处**，而成品纪要 **0 处**（下游 LLM 已自动修复） | 不做 |
| 片内一致性纠错（同实体按多数派改少数派） | 真实收益 **2.3%**（强）/ **4.4%**（弱）——人工漏改的恰恰多是**孤例**，没有第二处写法可对照 | 不做 |
| 拼音候选层独立立项 | 词典直命中 **0%**（词典与真实错误零重叠）；拼音只能生成候选，选不出答案 | 不立项 |
| 词表跨主题扫描 | 命中 3 处、**真阳性 0 处** | 降级为「注意力提示」 |
| `initial_prompt` 注入专名提示 | `宁德时代` 出现 23→36 次有效，但人名**零救回**（声学层错误，提示词只在解码先验层起作用） | 关闭 |
| `BatchedInferencePipeline` 提速 | 快 2.05x，但凭空多出幻觉文本（「请不吝点赞 订阅 转发 打赏支持…」），错别字明显增多 | 关闭 |
| 调 Nano 参数量提速 | `batch_size` / VAD 段长 / 砍热词 / 开双进程 **四组全部零收益**；且 `batch_size>1` 引入解码非确定性 | 关闭 |
| vLLM 提速（Windows） | 官方部署矩阵面向 Linux GPU 服务端；官方公布的 RTFx 340 是 **H100** 上的数值（与 4060 Ti 不可比） | Windows 原生不可行 |
| llama.cpp GGUF 加速 Nano | 官方 Windows 预编译包**只含 SenseVoiceSmall** 二进制；CUDA 包面向 arch 86（RTX 30 系），本机 NVIDIA RTX 4060 Ti 8GB 的 sm_89 不在覆盖内 | 要 GPU 必须自行编译 |

完整数据、样本与复现命令见 [docs/已否决的路线.md](docs/已否决的路线.md)。

---

## 工具清单

| 脚本 | 第三方依赖 | 作用 |
|---|---|---|
| `tools/verify_pack.py` | **无** | 校验转写产物：标题与基本信息、正文行时间戳前缀、元信息块、确定性红线 |
| `tools/verify_structure.py` | **无** | 校验结构化条目：元数据字段、章节齐全、要点表格化 |
| `tools/verify_coverage.py` | **无** | 改写前后覆盖比对：时间戳 / 章节标题 / 体积 |
| `tools/check_glossary.py` | **无** | 专名误识扫描与替换（支持只报告 / 自动替换） |
| `tools/baseline_errors.py` | pypinyin（可降级） | 错误类型分布、人工漏改率、分层纠错命中率 |
| `tools/funasr_adapter.py` | funasr + imageio-ffmpeg | Fun-ASR-Nano 引擎适配，输出与 faster-whisper 一致的 segments |

**术语**：「素材包」指一次转写的完整产物（Markdown），结构为 标题与基本信息 + 带 `[hh:mm:ss]` 的正文 + 机器可读元信息块。示例见 [`examples/sample-podcast-pack.md`](examples/sample-podcast-pack.md)。

**格式约定**：正文每行都要带 `[hh:mm:ss]` 前缀。这样每条结论都能回跳到音频时间点，也让「格式是否被破坏」可以被脚本客观判定，不需要靠肉眼。平台专属字段（如视频号、UP主）存在就随包保留，缺失不判错。

---

## 适用边界

- 结论来自**中文口播**场景（播客、访谈、解说、会议）。英文与多语种场景的引擎取舍可能不同
- 速度数据来自 **NVIDIA RTX 4060 Ti 8GB**。CPU 回退约为 GPU 的 1/6
- 「已否决的路线」都有明确的适用边界（Windows 原生 / 官方预编译产物 / 跨主题），不要外推成普适结论
- 对话内容的法律与伦理判断不在本项目范围内。转写他人音频请遵守当地法律与平台条款

---

## 来源

本项目的方法论与数据，来自对一个 B站视频总结工具的长期迭代。那套工具覆盖了本仓库的全部环节（抓音轨 → 双引擎转写 → 专名纠错 → 结构化条目），上面这些坑都是它在真实批次里踩出来的。

工具本体在 [bilibili-video-summary](https://github.com/Willson-Huang/bilibili-video-summary)。本项目把其中与平台无关的部分抽出来，让不用 B站 的人也能直接使用。

---

## License

[MIT](LICENSE)。转写他人音频时，音频内容版权归原权利人所有，请勿批量抓取或二次分发。
