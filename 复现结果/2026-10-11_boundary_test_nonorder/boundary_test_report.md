---
title: 边界测试复现报告：非时间顺序数据下 MemoTime 分解层行为
tags: [边界测试, 复现报告, MemoTime, 时间单调性, 适用边界]
date: 2026-10-11
status: 阶段A完成（分解层）· 阶段B（全链路玩具KG）可选待定
---

# 边界测试复现报告：非时间顺序数据下 MemoTime 分解层行为

> [!abstract] 一句话结论
> 在循环/并列/时间不可判/双时间轴/值震荡五类**非时间顺序**问题上，MemoTime 的分解层**全部产出矛盾、永假、虚构或不可用的子问题**——方法的"时间全序"假设被 9 题压力测试证实为硬边界；同时对照组暴露了"时间锚点不入约束"的主流程暗病，与主报告第 5 章的 62% 检索失败归因互相印证。

## 1. 实验目的与背景

**方法假设**（论文侧）：MemoTime 的 8 类分解模板、时间剪枝与 `temporal monotonicity` 验证，隐含假设问题的时间语义是**区间可比的全序**。MultiTQ/TimeQuestions 均由 TKG 四元组 `(s, r, o, t)` 生成，天然满足该假设——基准上看不见方法边界。

**测试问题**：当数据不满足时间单调/时间顺序（循环、并列、粗粒度不可判、双时间轴、值非单调）时，分解层会输出什么？

**位置**：分解 = 架构四组件之二（hierarchical control）的入口，其产物（子问题 + indicator + 时间约束）决定后续检索与剪枝的行为——分解层失效即可判定全链路失效，故阶段 A 只测分解层。

## 2. 实验设计

- **样本**：9 题 = 对照组 3（原数据集风格顺序型）+ 实验组 6（三类边界：循环×2、并列/不可判/双轴各 1、值震荡 1——完整 9 题见附录 A）
- **执行环境**：服务器 h3c，conda `MemoTime`，骨干 **qwen3-32b**（AWQ @8012，与在跑全量实验同一服务；本测试仅 9 次短 LLM 调用，对实验影响秒级）
- **调用方式**：直接调 `decompose_question(question)`——不经过 run.py、不写任何实验库（纯只读测试）
- **观察三点**：① `detect_question_type` 分类结果 ② 选用的分解模板 ③ 分解产物（子问题文本 + indicator 边 + 时间约束）的逻辑自洽性
- **判定标准**：产物需满足——约束与题目语义一致、无自相矛盾、不虚构图中不存在的关系边、能表达题目要求的运算（含"不可比则并列"的出口）

## 3. 结果

### 3.1 对照组（顺序型）

| # | 题型 | 分解产物 | 判定 |
|---|---|---|---|
| 1 | AFTER | 单子问题，`t2 > 2019` | ✅ 正常 |
| 2 | 序数 first | 单子问题，`first(t1)`，indicator 正确 | ✅ 正常 |
| 3 | **区间 between** | 拆成 "in t1"/"in t2"，**约束却是 `['Egypt','Egypt']`，2018/2020 未进入约束** | ⚠️ 意外缺陷 |

> [!note] 对照组意外发现（重要）
> 连正常区间题都出现**时间锚点不进约束**——与主复现报告第 5 章假设 1（时间约束未进入查询、Dyson 题多时间全量返回、62% 图可答却查不到）**互相印证**：该缺陷不是边界数据特有，而是主流程的结构性暗病。

### 3.2 实验组（非顺序型）：6/6 产出荒谬或不可用分解

| 类型 | 题目摘要 | 系统实际产出 | 病灶归类 |
|---|---|---|---|
| 循环 | next three Mondays 课表 | 拆出 `S1: [`（垃圾），仍报 "successfully parsed" | ①分类器误判为 `between`；②**解析器脆弱且质量检查失效** |
| 循环 | 2024 后下一个圣诞 | `?x --[Christmas]--> ?y` + `t2>2024` | **虚构图中不存在的关系边**；无周期/模运算语义 |
| 并列 | 同日开通的两条路谁先 | 约束 `first(?t1)` **且** `first(?t2)`；indicator 空边 | **逻辑自相矛盾**；无"检测并列→回答并列"出口 |
| 不可判 | 同年两个奖谁先 | 约束 `t1 < t2` | **永假约束**（同年则不成立）→ 充分性检查将死循环或乱选 |
| 双时间轴 | 记录时间 vs 发生时间 | `first(t1) & first(t2)`（互斥却并存）；两轴无法落图 | 数据模型单时间戳，**多轴无表达** |
| 值震荡 | 高点后的最低价 | `stock --[have]--> lowest price (t2)` | 把**区间聚合运算**降级为静态事实边——**工具箱无 interval-min 算子** |

### 3.3 四种荒谬模式归纳

1. **矛盾约束**：`first(t1) ∧ first(t2)`（双 first）、`first(t1) ∧ first(t2)` 互斥并存
2. **永假约束**：`t1 < t2` 施加于同年两事件
3. **虚构关系**：`Christmas` 作为可查关系边；周期知识无对应图结构
4. **表达力缺失**：区间 min/max 聚合、模运算（下一个周一）、多时间轴——超出 `(s,r,o,t)` 边查询 + 顺序模板的能力

## 4. 三硬结论

1. **分类器只有顺序语义的类**：`detect_question_type` 的全部类别（before_after/between/first_last/equal...）均为顺序族——非顺序题被**硬塞进错误模板**，8 个分解模板无一适配循环/并列/双轴语义
2. **约束生成对实验组系统性荒谬**：矛盾/永假/虚构/聚合缺失四模式齐出（表 3.2），分解产物无法支撑有效检索
3. **解析器脆弱性（阶段 A 的额外发现）**：LLM 输出稍偏离模板即产出 `S1: [` 类垃圾且标记 "successfully parsed"，质量分析（has_temporal_info 等）未能拦截——**此暗病可能同样影响主实验的分解质量**（主报告待补项）

## 5. 意义与关联

- **对论文**：构成 limitation 直接素材——方法的时间剪枝/单调性验证在非全序数据上会**误剪、误拒、误放或无模板可用**；TKG 基准的全序时间戳是该假设的"温床"（数据分布：顺序型信号 OVERLAP+ORDINAL+方向词 ≈56%）
- **对主复现**：对照组缺陷与第 5 章 62% 检索失败的假设 1 互证（锚点不入约束是结构性问题）
- **对本研究计划（双通道记忆）**：事实经验的"区间有效窗 [t_start,t_end]"**同样表达不了周期/并列/多轴**——设计需预留扩展：有效性谓词除区间外可为周期模式/时间点集合/布尔条件；至少在 limitation 中显式承认

## 6. 局限

1. 样本量 9 题、单骨干（qwen3-32b）——结论为**机制性失效证据**，不构成发生率统计
2. 仅测分解层（阶段 B 玩具 KG 全链路未执行）——分解已崩的前提下，检索层行为可推知但未直接观察
3. 解析器缺陷与语义缺陷部分混杂（循环题的垃圾产物源于解析而非分类）——归因已分列
4. 32B 与论文骨干配置存在 AWQ 量化差异（见主报告偏差清单）

## 7. 复跑方式

```bash
# 服务器 h3c
/home/cwf/miniconda3/envs/MemoTime/bin/python /home/cwf/boundary_decompose_test.py
# 脚本副本：本文件夹 boundary_decompose_test.py；原始输出：raw_output.txt
# 注意：仅读 LLM，不写实验库；依赖 .env 的 LLM_MODEL/QWEN_BASE_URL
```

## 附录 A：9 题样本全文

```
对照1 AFTER : Which city did Ali Reza visit after 2019?
对照2 区间  : What major event happened between 2018 and 2020 in Egypt?
对照3 序数  : Who was the first person to win the Nobel Prize in Physics?
实验1 循环  : What classes do I have on the next three Mondays?
实验2 循环  : When is the next Christmas after 2024?
实验3 并列  : Both Road A and Road B opened on 2019-05-01. Which one opened first?
实验4 不可判: He received Award X and Award Y in the same year 1995. Which one did he receive first?
实验5 双轴  : A symptom was recorded on 2024-04-10 but actually occurred on 2024-03-05. Which happened first?
实验6 值震荡: The stock hit its all-time high in 2021. What was the lowest price after that?
```

## 附录 B：产物清单

```
复现结果/2026-10-11_boundary_test_nonorder/
├── boundary_test_report.md        本报告
├── boundary_decompose_test.py     测试脚本（可复跑）
└── raw_output.txt                 9 题完整原始输出（证据存档）
```

**关联文档**：主复现报告 `复现结果/2026-10-08_qwen3-8b_tq_full/report.md`（第 5 章错误归因）· 研究计划 `研究计划-时序AgentMemory切入点.md`
