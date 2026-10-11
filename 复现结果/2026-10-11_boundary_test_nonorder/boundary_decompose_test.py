# -*- coding: utf-8 -*-
"""阶段A：分解层边界压力测试
9 题 = 3 对照（顺序型，原数据集风格）+ 6 实验（循环/并列/不可判/双时间轴/值震荡）
只调 decompose_question（纯 LLM 读取，不写任何实验库），观察：
  ① detect_question_type 判成什么类 ② 选了哪个分解模板 ③ 拆出的子问题/时间约束
"""
import os
import sys
from pathlib import Path

os.environ["HF_HUB_OFFLINE"] = "1"
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.chdir("/home/cwf/Code/MemoTime/memotime")
sys.path.insert(0, "/home/cwf/Code/MemoTime")  # memotime 包的父目录（项目根）

from memotime.kg_agent.decompose import decompose_question, detect_question_type

CASES = [
    # (组别, 题目)
    ("对照·AFTER", "Which city did Ali Reza visit after 2019?"),
    ("对照·区间", "What major event happened between 2018 and 2020 in Egypt?"),
    ("对照·序数", "Who was the first person to win the Nobel Prize in Physics?"),
    ("实验·循环", "What classes do I have on the next three Mondays?"),
    ("实验·循环", "When is the next Christmas after 2024?"),
    ("实验·并列", "Both Road A and Road B opened on 2019-05-01. Which one opened first?"),
    ("实验·不可判", "He received Award X and Award Y in the same year 1995. Which one did he receive first?"),
    ("实验·双时间轴", "A symptom was recorded on 2024-04-10 but actually occurred on 2024-03-05. Which happened first?"),
    ("实验·值震荡", "The stock hit its all-time high in 2021. What was the lowest price after that?"),
]


def main():
    for i, (group, q) in enumerate(CASES, 1):
        print("=" * 100)
        print(f"[{i}/9] [{group}] {q}")
        # 1) 题型检测
        try:
            qt = detect_question_type(q)
        except Exception as e:
            qt = f"DETECT_FAIL({e})"
        print(f"  detect_question_type -> {qt}")
        # 2) 分解
        try:
            dec = decompose_question(q)
        except Exception as e:
            print(f"  decompose EXCEPTION: {type(e).__name__}: {str(e)[:200]}")
            continue
        if dec is None:
            print("  decompose -> None")
            continue
        subs = getattr(dec, "subquestions", None) or []
        tvars = getattr(dec, "time_vars", None)
        print(f"  -> {len(subs)} subquestion(s) | time_vars={tvars}")
        for j, s in enumerate(subs, 1):
            txt = getattr(s, "text", None) or (s.get("text") if isinstance(s, dict) else str(s))
            ind = getattr(s, "indicator", None) or (s.get("indicator") if isinstance(s, dict) else None)
            print(f"     S{j}: {txt}")
            if ind is not None:
                print(f"         indicator: {str(ind)[:220]}")
    print("=" * 100)
    print("BOUNDARY TEST DONE")


if __name__ == "__main__":
    main()
