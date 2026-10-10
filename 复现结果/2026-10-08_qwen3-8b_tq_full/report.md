# MemoTime 复现报告：TimeQuestions × Qwen3-8B

> 生成日期：2026-10-10 · 结论等级：**当前实现受控复现**（非与论文严格可比，依据复现指南第 8 节第 6 条）

## 1. 实验对象

| 项 | 值 |
|---|---|
| 论文 | MemoTime, WWW 2026, [arXiv:2510.13614](https://arxiv.org/abs/2510.13614)（表 3：Qwen3-8B / TimeQuestions **Hits@1 = 49.4%**，IO 基线 28.5%） |
| 仓库 | 本地 `84ed588` + 未提交改动（6 项 bug 修复、BGE-M3 embedding 替换、vLLM 适配、UTF-8/编码修复，详见 `00_config_snapshot/git_diff_stat.txt`） |
| 骨干模型 | `Qwen/Qwen3-8B`，vLLM 0.31.0，`served-model-name=qwen3-8b`，**非思考模式**（`chat_template_kwargs.enable_thinking=false`），`max_tokens=2048`、温度 0.4（充分性/验证 0） |
| 运行环境 | 服务器 h3c（2×L20），conda env `MemoTime`（Python 3.11, torch 2.3.0+cu121）；评测主进程本地运行，LLM 经 `http://localhost:8011/v1` |
| 检索向量 | 实体/混合检索 `BAAI/bge-m3`（1024 维，离线缓存）；语义剪枝 `msmarco-distilbert-base-tas-b`；统一知识库 `all-MiniLM-L6-v2` |

## 2. 数据和评测

| 项 | 值 |
|---|---|
| 数据集 | TimeQuestions test = **3237 题**（全量执行，0 排除、0 失败题） |
| 图库 | 113,407 实体 / 239,386 边（全量索引，非 -n 子集） |
| 候选文件 | `questions_with_candidates_timequestions.json`：3237 题，非空候选 99.4%，平均 74.6 候选/题 |
| 预处理抽词 | Qwen3-8B（entity_try `USE_QWEN`，与骨干一致；抽词失败率 0） |
| 经验库 | **冷启动**（旧经验已清空备份）；评测期间在线增长，题序即实验条件 |
| 配置 | `config_id=cfg_d0784087671f`（retry=2, depth=3, branches=20, hybrid, unified, individual） |
| 评测口径 | 三层：`strict_hit`（**自定义严格**：时间答案按年匹配、实体答案归一化精确匹配）、`strict_exact`（完全字符串相等）、`local_verifier_hit`（本地宽松验证器，含包含/语义/时间格式匹配，仅作对照） |
| 逐题表 | `03_results/per_question_TimeQuestions.csv`（含 gold/prediction/strict/local 列） |

## 3. 主要结果

| 口径 | 数值 | 与论文 49.4% 的差距 |
|---|---:|---:|
| **strict_hit（自定义严格）** | **952/3237 = 29.41%** | −19.99 pp |
| strict_exact（完全相等） | 507/3237 = 15.66% | −33.74 pp |
| local_verifier_hit（宽松对照） | 1261/3237 = 38.96% | −10.44 pp |

> 论文为 exact match Hits@1，但其时间粒度归一化规则未公开；本地三种口径均**低于**论文值，因此差距不是口径放宽造成的假象。

### 按题型分组（strict / local）

| 题型 | 题数 | strict_hit | local_hit |
|---|---:|---:|---:|
| Temp.Ans | 1064 | 42.9% | 51.2% |
| Explicit | 1284 | 23.6% | 32.2% |
| Ordinal | 488 | 20.7% | 32.6% |
| Implicit | 274 | 21.5% | 36.5% |
| 混合题型（其余 7 类） | 127 | 22.0% | 34.6% |

时间值类（Temp.Ans）显著好于实体推理类——与"时间按年匹配"的严格口径更宽松有关，也与图中时间边覆盖更完整一致。

## 4. 过程指标

- **耗时**：保存跨度 2026-10-08 12:59 → 10-10 00:52 ≈ **35.9 小时**（含一次崩溃中断与恢复），平均 ~40 s/题；纯运行速率约 26–48 s/题
- **推理规模**：平均轨迹 **1.7 子问题步/题**；按每步 4–6 次 LLM 调用估算约 **7–10 次调用/题**（论文附录 8.75 次同量级；本地 token 统计缺失——已知遗留 bug，usage 未接入监控）
- **稳定性**：0 数据丢失。运行事故一次：3237 题全部落库后，收尾 `record_run` 因 `best_success_rate=NULL` 比较崩溃（已修复 None 防护）；服务器看门狗自动重启（`~/watchdog.log`），人工判定后停止第二轮以保持单轮口径，**第二轮仅覆盖 3 题（0.09% 错→对翻转）**
- **产物**：`experiments/experiments_timequestions.db`（question_results 3237 / trajectories / evidence_edges）、运行日志 `02_evaluation/eval_tq_full_server.log`（symlink → /tmp/eval_tq_full.log）

## 5. 偏差解释（与论文不可严格对齐的原因）

按复现指南第 2 节差异表逐项核对，并补充本次执行发现：

1. **记忆复用不完整**：`stepwise.py` 主流程 `pool_result=None`，指南指出的经验快捷复用分支不可达；仅存在经验写入与部分读取——"记忆增强"只实现了部分（论文消融中该模块贡献 −4.4pp）
2. **检索向量不同**：论文写 Sentence-BERT 稠密检索；本地为 BGE-M3 + FAISS HNSW/IVF。候选与证据排序随之改变（论文消融：去掉向量检索 −4.1pp，说明该链路敏感）
3. **生成长度**：本地 `max_tokens=2048` vs 论文附录 256——更长输出改变最终答案抽取
4. **思考模式**：Qwen3 非思考模式（论文未说明该开关）
5. **评分器**：`local` 口径含包含/语义/宽松匹配（38.96%）；`strict_hit` 为自定义口径（时间按年、实体按名）；两者均非论文原版 exact match
6. **宽度参数**：本地全局分支预算 `branches=20` ≠ 论文 `Wmax=3`；论文的首轮候选 80、排序权重等未逐项映射
7. **预处理模型**：关键词抽取用 Qwen3-8B（论文未公开其预处理模型）
8. **数据泄漏声明**：全量索引为**非 -n 全量版**（无答案实体参与索引构建）；但经验库评测期间在线增长（冷启动 + 题序条件已记录）
9. **轻微污染**：3 题（0.09%）经意外第二轮翻转为对，报告口径为单轮+此污染说明

## 6. 结论等级

- ✅ **跑通**：全量 3237 题端到端完成，零数据丢失，三层口径齐备，逐题可审计
- ✅ **当前实现受控复现**：在本地代码/BGE-M3/vLLM Qwen3-8B 口径下得到稳定可复算结果（strict 29.41% / local 38.96%）
- ❌ **与论文严格可比**：不成立——上述 1–7 项未逐项对齐前，本地结果不能直接与 49.4% 横向比较

**与论文差距的初步归因**（按指南第 7 节排查表）：本地宽松口径（39.0%）仍低于论文 49.4%，说明差距非纯评分口径问题；结合偏差清单，优先怀疑 **记忆复用不可达（1）+ 检索向量差异（2）+ 宽度参数未对齐（6）** 三项，其次为生成参数（3、4）。复现为 IO 基线未跑，无法验证论文表 3 的相对增幅口径。

## 7. 后续建议

1. 对齐记忆复用（修复 `pool_result` 路径）后重跑，量化记忆模块贡献
2. 以 Sentence-BERT 或论文指定模型重建检索索引对照
3. 参数对齐（Wmax=3、max_tokens=256）做消融
4. 补跑 IO 基线与 MultiTQ（54,584 题，候选准备需 ~15–30h + 评测数天）
5. 修复 token 统计（usage 接入 performance_monitor）以报告准确调用/成本

---

### 附：产物清单

```
复现结果/2026-10-08_qwen3-8b_tq_full/
├── 00_config_snapshot/   git HEAD、依赖版本、vLLM models、脱敏 .env
├── 01_data_prepare/      prepare_tq_full.log（全量数据准备日志）
├── 02_evaluation/        eval_tq_full_server.log（主日志）、崩溃归档、cold_start_backup/
├── 03_results/           per_question_TimeQuestions.csv、summary_TimeQuestions.md
├── evaluate_strict.py    独立严格评测脚本
├── report.md             本报告
└── watchdog 遗留         服务器 ~/watchdog.log（已随评测停止而退出）
```
