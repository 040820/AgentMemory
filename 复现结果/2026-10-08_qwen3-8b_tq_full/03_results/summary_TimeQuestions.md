# 严格评测汇总 — TimeQuestions

- config_name: `qwen3_8b_tq_full_20261008`
- 题数: 3237
- **strict_hit（时间按年/实体按名，自定义口径）: 952/3237 = 29.41%**
- strict_exact（完全字符串相等）: 507/3237 = 15.66%
- local_verifier_hit（本地宽松判定，对照）: 1261/3237 = 38.96%

## 按题型分组

| 题型 | 题数 | strict_hit | local_hit |
|---|---:|---:|---:|
| ['Explicit', 'Implicit'] | 5 | 2 (40.0%) | 2 (40.0%) |
| ['Explicit', 'Ordinal'] | 17 | 6 (35.3%) | 9 (52.9%) |
| ['Explicit', 'Temp.Ans'] | 5 | 3 (60.0%) | 3 (60.0%) |
| ['Explicit'] | 1284 | 303 (23.6%) | 414 (32.2%) |
| ['Implicit', 'Ordinal'] | 6 | 1 (16.7%) | 1 (16.7%) |
| ['Implicit', 'Temp.Ans', 'Ordinal'] | 1 | 0 (0.0%) | 0 (0.0%) |
| ['Implicit', 'Temp.Ans'] | 11 | 5 (45.5%) | 6 (54.5%) |
| ['Implicit'] | 274 | 59 (21.5%) | 100 (36.5%) |
| ['Ordinal', 'Temp.Ans'] | 79 | 15 (19.0%) | 21 (26.6%) |
| ['Ordinal'] | 488 | 101 (20.7%) | 159 (32.6%) |
| ['Temp.Ans', 'Explicit'] | 1 | 1 (100.0%) | 1 (100.0%) |
| ['Temp.Ans', 'Ordinal'] | 2 | 0 (0.0%) | 0 (0.0%) |
| ['Temp.Ans'] | 1064 | 456 (42.9%) | 545 (51.2%) |

> 口径说明：论文未公开 exact match 对时间粒度的规则；本脚本对时间答案按年份匹配、
> 对实体答案按归一化名称精确匹配，标注为「自定义严格口径」。local_verifier_hit 允许
> 包含/语义/宽松匹配，不能直接等同论文 Hits@1。