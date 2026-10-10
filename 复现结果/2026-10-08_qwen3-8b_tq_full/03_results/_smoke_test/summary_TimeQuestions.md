# 严格评测汇总 — TimeQuestions

- config_name: `vllm_qwen3_8b_test`
- 题数: 5
- **strict_hit（时间按年/实体按名，自定义口径）: 1/5 = 20.00%**
- strict_exact（完全字符串相等）: 0/5 = 0.00%
- local_verifier_hit（本地宽松判定，对照）: 2/5 = 40.00%

## 按题型分组

| 题型 | 题数 | strict_hit | local_hit |
|---|---:|---:|---:|
| ['Explicit'] | 2 | 0 (0.0%) | 0 (0.0%) |
| ['Ordinal'] | 1 | 0 (0.0%) | 1 (100.0%) |
| ['Temp.Ans'] | 2 | 1 (50.0%) | 1 (50.0%) |

> 口径说明：论文未公开 exact match 对时间粒度的规则；本脚本对时间答案按年份匹配、
> 对实体答案按归一化名称精确匹配，标注为「自定义严格口径」。local_verifier_hit 允许
> 包含/语义/宽松匹配，不能直接等同论文 Hits@1。