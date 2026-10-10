# -*- coding: utf-8 -*-
"""独立严格 Hits@1 评测脚本（按复现指南第 6/7 节要求）。

与本地 answer_verifier 的宽松判定分开，从逐题原始答案重新计分：
  - strict_exact  : 预测候选与金标准归一化后完全字符串相等（最严）
  - strict_hit    : Entity 类按归一化精确匹配；Value(时间) 类按年份匹配
                    （自定义口径：论文 exact match 对时间粒度的规则未公开，
                     本口径 = "时间按年、实体按名"，报告中须标注）
  - local_hit     : 实验库中本地验证器的 is_correct（宽松，仅对照）

用法：
  python evaluate_strict.py --dataset TimeQuestions \
      --config-name qwen3_8b_tq_full_20261008 \
      --out-dir 03_results
"""
import argparse
import ast
import csv
import json
import re
import sqlite3
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parents[2]


# ---------- 文本归一 ----------
def norm_text(s: str) -> str:
    s = str(s).strip().lower()
    s = s.replace("_", " ").replace("-", " ")
    s = re.sub(r"[^\w\s():/]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def extract_years(s: str):
    return set(re.findall(r"(?<!\d)(1[89]\d{2}|20\d{2})(?!\d)", str(s)))


# ---------- 预测答案解析 ----------
def parse_prediction(final_answer: str):
    """从 'So the answer is: X, or Y, or [Z] on T' 提取候选值列表"""
    if not final_answer:
        return []
    text = str(final_answer).strip()
    text = re.sub(r"^.*?so the answer is[:\s]*", "", text, flags=re.I).strip()
    parts = re.split(r",\s*or\s*", text)
    cands = []
    for p in parts:
        p = p.strip().rstrip(".")
        p = re.sub(r"^\[|\]$", "", p).strip()            # 去 [..] 包裹
        p = re.sub(r"\s+on\s+(1[89]\d{2}|20\d{2}).*$", "", p)  # 去 ' on 1972' 尾巴
        if p:
            cands.append(p.strip())
    return cands or [text]


# ---------- 金标准解析（TimeQuestions Answer 字段） ----------
def parse_golden(answer_field):
    """返回 (entity_labels, time_values)"""
    try:
        items = ast.literal_eval(str(answer_field))
    except Exception:
        return [], []
    if isinstance(items, dict):
        items = [items]
    entities, times = [], []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        if it.get("AnswerType") == "Entity":
            lab = it.get("WikidataLabel")
            if lab:
                entities.append(str(lab))
        else:
            val = it.get("AnswerArgument")
            if val:
                times.append(str(val))
    return entities, times


def judge(pred_cands, entities, times):
    """返回 (strict_exact, strict_hit)"""
    pred_norm = {norm_text(p) for p in pred_cands}
    pred_years = set()
    for p in pred_cands:
        pred_years |= extract_years(p)

    strict_exact = False
    strict_hit = False

    for lab in entities:
        g = norm_text(lab)
        if g and g in pred_norm:
            strict_exact = True
            strict_hit = True
            break

    if not strict_hit and times:
        for t in times:
            gy = extract_years(t)
            # 完整字符串也算
            if norm_text(t) in pred_norm:
                strict_exact = True
                strict_hit = True
                break
            # 时间按年匹配
            if gy and (gy & pred_years):
                strict_hit = True
                break
            # 纯年份金标准
            if not gy and norm_text(t) in pred_norm:
                strict_hit = True
                break

    return strict_exact, strict_hit


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="TimeQuestions")
    ap.add_argument("--config-name", required=True, help="实验库中的 config_name")
    ap.add_argument("--out-dir", default="03_results")
    ap.add_argument("--db", default=None)
    args = ap.parse_args()

    db_path = Path(args.db) if args.db else PROJECT_ROOT / "experiments" / f"experiments_{args.dataset.lower()}.db"
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 金标准 + 题型
    ds_dir = PROJECT_ROOT / "Data" / args.dataset
    test_path = ds_dir / "test.json"
    with open(test_path, "r", encoding="utf-8") as f:
        test_items = json.load(f)
    if args.dataset == "TimeQuestions":
        id_field, q_field, ans_field = "Id", "Question", "Answer"
        type_field = "Temporal question type"
    else:
        id_field, q_field, ans_field = "quid", "question", "answers"
        type_field = "qtype"
    gold = {str(t.get(id_field)): t for t in test_items}

    # 预测
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT q.qid, q.question, q.final_answer, q.is_correct "
        "FROM question_results q JOIN configurations c ON q.config_id = c.config_id "
        "WHERE c.config_name = ?", (args.config_name,)
    ).fetchall()
    conn.close()
    if not rows:
        print(f"❌ no results for config_name={args.config_name}")
        sys.exit(1)

    out_rows = []
    n = se = sh = lh = 0
    by_type = {}
    for qid, question, pred, local_hit in rows:
        item = gold.get(str(qid), {})
        ents, times = parse_golden(item.get(ans_field))
        pred_cands = parse_prediction(pred or "")
        strict_exact, strict_hit = judge(pred_cands, ents, times)
        t_raw = item.get(type_field, "")
        try:
            tkey = ",".join(ast.literal_eval(t_raw)) if t_raw else "unknown"
        except Exception:
            tkey = str(t_raw) or "unknown"

        n += 1
        se += strict_exact
        sh += strict_hit
        lh += bool(local_hit)

        st = by_type.setdefault(tkey, [0, 0, 0])
        st[0] += 1
        st[1] += strict_hit
        st[2] += bool(local_hit)

        out_rows.append({
            "dataset": args.dataset,
            "qid": qid,
            "qtype": tkey,
            "question": question,
            "gold_entities": "|".join(ents),
            "gold_times": "|".join(times),
            "prediction": (pred or "").replace("\n", " ")[:500],
            "pred_candidates": "|".join(pred_cands[:10]),
            "strict_hit": int(strict_hit),
            "strict_exact": int(strict_exact),
            "local_verifier_hit": int(bool(local_hit)),
        })

    # 逐题 CSV
    csv_path = out_dir / f"per_question_{args.dataset}.csv"
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(out_rows[0].keys()))
        w.writeheader()
        w.writerows(out_rows)

    # 汇总
    lines = [
        f"# 严格评测汇总 — {args.dataset}",
        f"",
        f"- config_name: `{args.config_name}`",
        f"- 题数: {n}",
        f"- **strict_hit（时间按年/实体按名，自定义口径）: {sh}/{n} = {sh/n*100:.2f}%**",
        f"- strict_exact（完全字符串相等）: {se}/{n} = {se/n*100:.2f}%",
        f"- local_verifier_hit（本地宽松判定，对照）: {lh}/{n} = {lh/n*100:.2f}%",
        f"",
        f"## 按题型分组",
        f"",
        f"| 题型 | 题数 | strict_hit | local_hit |",
        f"|---|---:|---:|---:|",
    ]
    for tkey, (c, s, l) in sorted(by_type.items()):
        lines.append(f"| {tkey} | {c} | {s} ({s/c*100:.1f}%) | {l} ({l/c*100:.1f}%) |")
    lines += [
        "",
        "> 口径说明：论文未公开 exact match 对时间粒度的规则；本脚本对时间答案按年份匹配、",
        "> 对实体答案按归一化名称精确匹配，标注为「自定义严格口径」。local_verifier_hit 允许",
        "> 包含/语义/宽松匹配，不能直接等同论文 Hits@1。",
    ]
    summary_path = out_dir / f"summary_{args.dataset}.md"
    summary_path.write_text("\n".join(lines), encoding="utf-8")

    print("\n".join(lines))
    print(f"\n逐题表: {csv_path}")
    print(f"汇总:   {summary_path}")


if __name__ == "__main__":
    main()
