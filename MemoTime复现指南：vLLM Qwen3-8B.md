---
title: MemoTime 复现指南：vLLM Qwen3-8B
tags: [论文复现, 时序知识图谱, MemoTime, Qwen3, vLLM]
date: 2026-10-07
---

# MemoTime 复现指南：vLLM Qwen3-8B

> [!abstract] 本次复现的边界
> 使用本地 `D:\JB\PythonProject\Agent_code\MemoTime`、Conda 环境 `MemoTime` 和服务器上的 vLLM Qwen3-8B，先确认推理链可运行，再做受控样本评测，最后才尝试论文的全量指标。当前本地仓库有大量未提交改动，以下命令针对**当前本地代码**，不能自动视为作者论文提交版本的精确复现。本文是操作指南；撰写时没有启动推理、重建索引或改动该仓库。

## 1. 先定义要复现什么

论文任务是：给定问题和时间知识图谱，输出实体或时间答案。主要指标为 Hits@1。论文附录统计：MultiTQ 测试集 54,584 题，TimeQuestions 测试集 3,237 题。论文表 3 中，Qwen3-8B 的结果如下：

| 数据集 | IO（直接回答） | MemoTime | 这次实验的记录栏 |
| --- | ---: | ---: | --- |
| MultiTQ | 2.5% | **57.0%** | 待填 |
| TimeQuestions | 28.5% | **49.4%** | 待填 |

这两个 MemoTime 数字是**论文参考值**，不是 5 题或 20 题小测的合格线。论文表 3 的 `↑` 列计算的是**相对增幅**，例如 MultiTQ 的 `(57.0−2.5)/2.5=21.8`，不是 `57.0/2.5` 的最终倍数。论文消融实验使用 GPT-4o-mini，不应直接拿其 64.2% 与本次 Qwen3-8B 作同配置比较。

建议分四级记录结果：① API 连通、模型 ID 正确；② 5 题端到端运行；③ 固定题目列表的 20～200 题定位误差；④ 全量测试集。只有第④级、相同数据/评测口径、确认经验库状态时，才讨论“接近论文分数”。

## 2. 当前本地代码与论文设定的关键差异

| 项目 | 论文描述 | 当前本地代码/状态 | 影响 |
| --- | --- | --- | --- |
| 模型 | Qwen3-8B 是表 3 的骨干之一 | `memotime/kg_agent/llm.py` 通过模型名中小写 `qwen` 路由；发送的 `model` 原样传给 vLLM | vLLM 的 `/v1/models` ID 必须与传入 ID 一致；`Qwen3-8B` 大写开头不会进入该分支 |
| 思考模式 | 附录没有给出 Qwen3 的具体开关 | 有 `QWEN_BASE_URL` 时传 `chat_template_kwargs.enable_thinking=False` | 应在复现记录中标明“非思考模式” |
| 生成长度 | 附录最大生成 256 tokens | Qwen 分支统一设置 `max_tokens=2048`；探索默认温度 0.4，部分验证/最终回答调用温度 0 | token 限额不一致，不能声称参数完全相同 |
| 检索向量 | 论文写 Sentence-BERT 稠密检索 | 本地实体/混合检索用 `BAAI/bge-m3`；统一知识库还加载 `all-MiniLM-L6-v2` | 模型和索引变化会改变候选及成绩 |
| 经验复用 | 论文主张检索并回写成功轨迹 | 本地 `stepwise.py` 主流程把 `pool_result=None`，该处快捷复用分支不可达；仍存在部分经验写入和别处读取逻辑 | 不能仅因启用 `--unified-knowledge` 就宣称完整复现论文记忆机制 |
| 评分 | 论文称 exact match Hits@1 | 本地 `answer_verifier.py` 默认还允许包含、时间格式、语义和宽松匹配 | 本地“success rate”可能高于严格 Hits@1，需另算严格指标 |
| 结果保存 | 单次评测 | `experiments/experiments_<dataset>.db` 可保存结果；同配置已答对的题会跳过；配置按内容哈希复用，`--name` 只改显示名 | 重跑统计可能混入旧答案；必须检查 `config_id`，或用新的 `--config-name` 改变配置内容并核对实际执行题数 |

此外，本地 `run.py` 的帮助文字说 `--questions` 默认 50、`--skip` 默认 5，但 `config.py` 当前默认值分别为 200、0；请始终显式传这两个参数。`--type` 先按 `--skip/--questions` 切片，再按题型过滤，因此 `--type before_last --questions 50` 不是“运行 50 道 before_last 题”。TimeQuestions 的题型字段与该 CLI 的四个 MultiTQ 风格选项也不完全一致。

## 3. 运行前检查（不打印密钥）

1. 在项目根目录记录 `git rev-parse HEAD` 和 `git status --short`。目前检查到的 HEAD 为 `84ed5888cc091b1a15ba6c2b2be4477584e972b8`，且源码、缓存、实验库均有本地改动；全量实验应另保存代码快照与配置，不要对现有目录执行 `reset/clean`。
2. 你的 `.env` 已有 `QWEN_BASE_URL` 与 `LLM_MODEL` 等键。只核对 `QWEN_BASE_URL` 指向**从运行 MemoTime 的 Windows 机器可访问**的 vLLM 地址，通常以 `/v1` 结尾；不要把密钥或 URL 连同认证信息写进公开日志。
3. 用 vLLM 的 `/v1/models` 查询实际 `id`。本地代码要求该 ID **包含小写 `qwen`**，并且 `--model` 值必须与服务器公告的 ID 完全一致。若服务器只公告 `Qwen3-8B` 或模型绝对路径，先调整 vLLM 的 `--served-model-name` 为例如 `qwen3-8b`，或修改代码实现大小写无关路由并保留真正的服务端 ID；不要只把大小写换了就盲跑。
4. 本地 Qwen 分支将 `DASHSCOPE_API_KEY`（默认占位 `XXX`）放在 `QWEN_API_KEY` 前面。无认证的 vLLM 通常可接受占位 key；若服务器设置了 `--api-key`，当前优先级可能造成 401。需要让客户端实际提交服务器要求的 key，且不要在笔记/日志中写值。
5. 当前 TimeQuestions 已有图数据库、实体索引、关系嵌入缓存和 **20 道**带候选实体的问题，20 题均非空候选；其 `hybrid_cache` 目前只有 `hybrid_data_cache.pkl`，缺少 `hybrid_index.bin` 与 `hybrid_embeddings.npy`，**不能认为混合索引已完成**。MultiTQ 当前只看到原始图文件和 `test.json`，缺少准备后的数据库及索引。

可以在激活环境后运行如下**只读连通性检查**。它只输出模型 ID，不输出 URL 或密钥：

```powershell
cd D:\JB\PythonProject\Agent_code\MemoTime
conda activate MemoTime
$env:PYTHONIOENCODING = 'utf-8'
@'
import os
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI
load_dotenv(Path.cwd() / '.env')
base = os.getenv('QWEN_BASE_URL')
assert base and base.rstrip('/').endswith('/v1'), '请检查 QWEN_BASE_URL（应以 /v1 结尾）'
client = OpenAI(base_url=base, api_key=os.getenv('DASHSCOPE_API_KEY') or 'XXX')
print([model.id for model in client.models.list().data])
'@ | python -
```

上述检查的 key 写法特意模拟当前 `llm.py` 的优先级。若此处 401，先修正认证再运行完整 Agent。

## 4. 第一阶段：用已有 TimeQuestions 文件做 5 题端到端检查

下面假设 `/v1/models` 返回 **`qwen3-8b`**；若不是，请先按上一节解决模型 ID。为降低历史结果复用，实验名和配置名都用新的值；运行时仍要检查实际 `config_id` 与经验库路径。时间和正确率均只用于诊断，不用于论文对比。

```powershell
cd D:\JB\PythonProject\Agent_code\MemoTime
conda activate MemoTime
$env:PYTHONIOENCODING = 'utf-8'
python memotime\run.py --dataset TimeQuestions --model qwen3-8b --questions 5 --skip 0 --retries 2 --depth 3 --branches 20 --no-hybrid --unified-knowledge --storage-mode individual --config-name qwen3_8b_tq_smoke_20261007 --name qwen3_8b_tq_smoke_20261007
```

成功的最低标准：日志显示模型为目标 Qwen3-8B；5 道题实际进入推理；每题可以读取候选实体、检索图事实、生成最终答案及验证结果；没有 401/404、空答案、JSON 解析失败或 embeddings 模型下载错误。本轮显式关闭混合检索，只检验基本链路，**不能把其分数与论文完整方法相比**。即使 5 题准确率很低，也先逐条核对证据链。若不希望这次写入正式实验库，可加 `--no-save`，但它只关闭实验结果库保存，**不保证所有经验/缓存写入都关闭**。

若已有 TimeQuestions 候选文件是 20 题，可在新的 `--config-name` 和 `--name` 下把 `--questions 5` 改为 `--questions 20` 做受控小样本；运行前确认候选文件和实体索引来自同一轮数据准备。现有索引可能是仅覆盖最初 20 题的子集，不应直接把同一个索引用于全量评价。

## 5. 第二阶段：准备 MultiTQ 与全量数据

`Data/prepare_datasets.py` 的顺序是建 SQLite 图库 → 建实体向量索引 → 为问题生成候选实体 → 可选建混合索引和关系嵌入。`-n N` 不只是缩短题目文件，还会让**首次创建的实体索引**只覆盖前 N 题的相关子图；该索引一旦存在，后续运行会直接跳过构建。更关键的是，子集索引的种子收集代码还读取测试题的**标准答案实体**，所以这种索引适合排障，**不能用它的成绩论证无泄漏的论文复现**。要做全量评测，须在隔离工作副本中重新建立**全量实体索引**，并核查其规模，不能把小样本索引沿用到全量。

首次 MultiTQ 小规模准备（会写入当前仓库；执行前建议复制工作目录保留现有缓存）：

```powershell
cd D:\JB\PythonProject\Agent_code\MemoTime
conda activate MemoTime
$env:PYTHONIOENCODING = 'utf-8'
python Data\prepare_datasets.py --dataset MultiTQ -n 5
```

这一步不会自动把混合检索所需的 `hybrid_index.bin` 等文件建齐。小规模排障可以先用 `--no-hybrid` 跑 5 题，确认图检索和模型通路；它不代表论文完整方法。若要启用 `--hybrid`，再执行相应数据集的 `--build-hybrid --build-embeddings` 准备，并确认产物存在。**注意 `--build-hybrid` 对整张图的事实编码，`-n 5` 不会把这一步缩成仅 5 题，可能耗时并占用大量内存/GPU。**

完整评测应在专用工作副本中，对 TimeQuestions 与 MultiTQ 分别执行全量准备（不带 `-n`），逐一核对：问题数为 3,237 / 54,584；带候选的问题覆盖率；非空候选比例；图数据库边数；实体索引和混合索引实际覆盖的实体/事实数。预处理中的 `Data/entity_try.py` 会优先调用 **MiMo** 抽取关键词，失败后退回启发式关键词；所以仅部署 Qwen3-8B 并不意味着预处理全程使用 Qwen。务必记录 MiMo 是否真的被调用及失败比例，否则候选实体质量可能与预期不同。

全量准备命令形式如下；运行成本可能很高，尤其 MultiTQ 的 54,584 道题及整图 BGE-M3 编码：

```powershell
python Data\prepare_datasets.py --dataset TimeQuestions --build-hybrid --build-embeddings
python Data\prepare_datasets.py --dataset MultiTQ --build-hybrid --build-embeddings
```

此命令在已有索引时会复用它。只有确认该索引是全量版本后，才能将对应运行称为全量复现；不要为“重建”直接删除已有用户数据。

## 6. 第三阶段：正式评测与结果记录

在已确认全量候选、索引、模型 ID、经验库状态后，再分别运行。以下以无认证服务公告 `qwen3-8b` 为例；每项必须用新实验名，且不要混入此前的 smoke 结果：

```powershell
python memotime\run.py --dataset TimeQuestions --model qwen3-8b --questions 3237 --skip 0 --retries 2 --depth 3 --branches 20 --hybrid --unified-knowledge --storage-mode individual --config-name qwen3_8b_tq_full_20261007 --name qwen3_8b_tq_full_20261007
python memotime\run.py --dataset MultiTQ --model qwen3-8b --questions 54584 --skip 0 --retries 2 --depth 3 --branches 20 --hybrid --unified-knowledge --storage-mode individual --config-name qwen3_8b_multitq_full_20261007 --name qwen3_8b_multitq_full_20261007
```

论文给出最大宽度 3、深度 3、首轮候选 80 等设定；当前 CLI 的 `--branches 20` 是本地代码全局分支预算，并不等于论文 `Wmax=3`。这些命令是本地可执行的对照基线，**不是论文所有超参数的逐项映射**。若要严格复现论文，还需逐模块核对宽度、排序权重、记忆冷启动样例与容量。

正式实验必须记录：Git commit 与工作树 diff；Qwen3-8B 权重 revision、量化方式、vLLM 版本及 `served-model-name`；思考模式；所有温度和 token 限额；BGE-M3/Sentence-BERT 模型版本；数据文件哈希、样本 ID 顺序；`config_id` 与经验库存放路径、初始条目数、运行后条目数、读命中数；运行用时及每题 LLM 调用数。`--name` 不是实验库隔离手段：代码先按配置哈希找旧 `config_id`，命中后会复用旧结果；`--config-name` 会影响配置里的统一知识库路径和哈希，但主推理器另有内部配置命名，仍须从日志核对经验存储目录。经验库在同一次评测中持续更新时，**题目顺序本身也是实验条件**。想研究记忆收益，应在相同题集与顺序上分别做冷启动和受控热启动，而不是让测试集旧答案或旧经验意外回流。

查询已保存结果可使用 `python memotime\run.py --dataset TimeQuestions --model qwen3-8b --name <实验名> --result`；但论文的 Hits@1 应再用独立评测脚本从逐题原始答案计算。当前本地验证器默认允许包含和语义匹配，不能把控制台 `success rate` 直接等同论文 exact match。独立脚本需事先固定大小写、别名、日期粒度、多标准答案的归一化规则；若无法确认论文完全相同的规则，应将结果标为“自定义严格匹配口径”，保留逐题预测供复核。

## 7. 如何分析结果

| 现象 | 优先查看 | 常见解释 |
| --- | --- | --- |
| 401/404、模型不响应 | `/v1/models` 的 ID、URL、key、`llm.py` 路由 | 大小写、服务端模型别名或 key 优先级不匹配 |
| 题目正常读取但候选为空 | `questions_with_candidates_*.json`、索引构建日志 | MiMo 抽词失败、子集索引覆盖不足、语义阈值过高 |
| 候选正确但时间顺序错 | 分解树、时间约束、检索边时间戳 | 局部证据正确，跨子问题窗口未同步 |
| 证据正确但答案错 | 最终提示词、`max_tokens`、思考模式、答案解析 | 截断、输出格式或生成阶段选错候选 |
| 本地准确率高于论文 | 严格 Hits@1 重新计分、旧结果跳题、经验库内容 | 宽松匹配或测试信息回流 |
| 本地准确率低于论文 | 候选覆盖率、图/索引版本、当前代码差异 | 预处理与记忆链不同、模型或采样不同 |

建议保存一张逐题表：`dataset, qid, qtype, gold, prediction, strict_hit, local_verifier_hit, candidates_count, retrieved_evidence, temporal_valid, memory_hit, llm_calls, latency, error_stage`。先报总体 Hits@1，再按 `Equal / Before-After / First-Last / Equal-Multi / After-First / Before-Last` 分组，最后分析错误究竟发生在**实体识别、检索、时间约束、答案生成还是评分**。小样本应报告具体题数；例如 5 题每错 1 题就变动 20 个百分点，不能据此判断是否复现 57.0% 或 49.4%。

论文表 6 的平均 8.75 次 LLM 调用、43.11 秒/题是其环境下的效率参考；本地 vLLM 的吞吐、网络、上下文长度、索引缓存都会改变时间。若答案分数相近但耗时不同，应单独报告性能，不应把速度差直接解释为方法失效。

## 8. 最终复现报告建议结构

1. **实验对象**：论文版本、仓库 commit、未提交改动、模型权重与 vLLM 配置。
2. **数据和评测**：两数据集测试数、实际执行数、排除/失败题数、严格 Hits@1 算法。
3. **主要结果**：论文参考值、本地严格值、本地宽松验证值；逐题差异和置信区间。
4. **过程指标**：候选召回、图证据覆盖、时间约束有效率、经验命中、LLM 调用和延迟。
5. **偏差解释**：重点说明当前本地代码的 `pool_result=None`、BGE-M3、2048 token、非思考模式和宽松验证器。
6. **结论等级**：跑通 / 当前实现受控复现 / 与论文严格可比。不要把前两种写成“复现论文 SOTA”。

## 资料与代码定位

- 论文：[MemoTime, arXiv:2510.13614v3](https://arxiv.org/pdf/2510.13614)，重点看表 3（PDF 第 8 页）、表 6（第 11 页）和附录 D（第 13 页）。
- 作者仓库：[SteveTANTAN/MemoTime](https://github.com/SteveTANTAN/MemoTime)。
- 本地入口：`D:\JB\PythonProject\Agent_code\MemoTime\memotime\run.py`；实验编排 `memotime\main.py`；模型路由 `memotime\kg_agent\llm.py`；数据准备 `Data\prepare_datasets.py`；评分 `memotime\kg_agent\answer_verifier.py`。
- vLLM/Qwen 参数：[Qwen-Agent 官方配置文档](https://qwenlm.github.io/Qwen-Agent/en/guide/get_started/configuration/)（vLLM 的 `chat_template_kwargs.enable_thinking` 与本地 OpenAI 兼容调用格式）。
