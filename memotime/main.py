#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================
# file: main.py
# =============================
# 本文件只负责组织一次实验，不实现子问题分解或图谱检索。
# 调用链：run.py 解析参数 → run_experiment 配置并逐题运行
#       → stepwise.solve_with_decomposition 求解 → 保存答案及证据。
from data_process import load_questions
from kg_agent import TemplateRegistry, Agent, solve_with_decomposition
from kg_agent.performance_monitor import get_performance_monitor, start_question_monitoring, end_question_monitoring
from tqdm import tqdm
import json
import sys

# TPKGConfig 保存运行时配置；apply_config/load_preset 分别处理显式参数和预设。
from config import TPKGConfig, load_preset, apply_config
from experiment_database import get_experiment_db


def run_experiment(args=None):
    """运行一次实验：设置参数、读取题目、逐题求解，并记录可用的结果。

    args 是 run.py 传来的命令行参数对象；为 None 时使用环境变量中的旧版配置。
    此函数打印实验统计，不返回逐题结果；逐题结果由求解器返回后按配置写入实验库。
    """
    
    # 命令行路径：先构造配置字典，再一次性交给 apply_config 更新 TPKGConfig。
    # 更新顺序为数据集 → 预设 → 显式参数；相同键以后写入的值为准。
    
    if args:
        config_dict = {}
        
        # 先确定数据集，以便后面按当前数据集选择路径和字段名。
        if hasattr(args, 'dataset') and args.dataset:
            config_dict['dataset'] = args.dataset
        
        # 预设是一组默认实验参数；后面的显式参数可以覆盖其中的同名项。
        if args.preset:
            from config import ExperimentPresets
            presets = {
                "baseline": ExperimentPresets.baseline(),
                "fast_mode": ExperimentPresets.fast_mode(),
                "accuracy_mode": ExperimentPresets.accuracy_mode(),
                "full_optimization": ExperimentPresets.full_optimization(),
                "equal_type_test": ExperimentPresets.equal_type_test(),
                "experience_pool_test": ExperimentPresets.experience_pool_test(),
            }
            if args.preset in presets:
                config_dict.update(presets[args.preset])
        
        # None 表示命令行没有指定该项，保留预设或 TPKGConfig 原值。
        # 重试、分解深度和分支数分别限制整题重试、递归细化和子问题数量。
        if args.retries is not None:
            config_dict['max_retries'] = args.retries
        if args.depth is not None:
            config_dict['max_depth'] = args.depth
        if args.branches is not None:
            config_dict['max_total_branches'] = args.branches
        if args.hybrid is not None:
            config_dict['use_hybrid_retrieval'] = args.hybrid
        # experience pool and template learning parameters have been removed
        if hasattr(args, 'unified_knowledge') and args.unified_knowledge is not None:
            config_dict['use_unified_knowledge_store'] = args.unified_knowledge
        if args.questions is not None:
            config_dict['max_questions'] = args.questions
        if args.skip is not None:
            config_dict['skip_questions'] = args.skip
        if args.type:
            config_dict['filter_question_type'] = args.type
        if args.entities is not None:
            config_dict['max_candidate_entities'] = args.entities
        if args.model:
            config_dict['default_llm_model'] = args.model
        
        # 经验库的存储模式、共享回退和配置名由独立参数控制。
        if hasattr(args, 'storage_mode'):
            config_dict['storage_mode'] = args.storage_mode
        if hasattr(args, 'enable_shared_fallback'):
            config_dict['enable_shared_fallback'] = args.enable_shared_fallback
        if hasattr(args, 'config_name') and args.config_name:
            config_dict['config_name'] = args.config_name
        
        # 在读数据或创建实验记录前完成配置，避免路径和配置哈希不一致。
        apply_config(config_dict)
        
        # 元数据用于标识实验；--no-save 只控制实验结果数据库的保存流程。
        EXPERIMENT_NAME = args.name if hasattr(args, 'name') and args.name else None
        EXPERIMENT_DESC = args.desc if hasattr(args, 'desc') and args.desc else ""
        EXPERIMENT_TAGS = args.tags.split(",") if hasattr(args, 'tags') and args.tags else []
        AUTO_SAVE = not (hasattr(args, 'no_save') and args.no_save)
        
    else:
        # 直接运行 main.py 时走兼容路径：预设及实验元数据来自环境变量。
        import os
        
        # read preset from environment variables
        EXPERIMENT_PRESET = os.getenv("EXPERIMENT_PRESET", None)
        if EXPERIMENT_PRESET:
            load_preset(EXPERIMENT_PRESET)
        
        # experiment metadata
        EXPERIMENT_NAME = os.getenv("EXPERIMENT_NAME", None)
        EXPERIMENT_DESC = os.getenv("EXPERIMENT_DESC", "")
        EXPERIMENT_TAGS = os.getenv("EXPERIMENT_TAGS", "").split(",") if os.getenv("EXPERIMENT_TAGS") else []
        AUTO_SAVE = os.getenv("AUTO_EXPERIMENT", "true").lower() == "true"
    
    # AUTO_SAVE 为 False 时不创建实验数据库对象，后面的跳题和落库逻辑也随之跳过。
    # config_id 由完整配置计算哈希后取得，同一配置可复用已有记录。
    exp_db = None
    config_id = None
    config_name = EXPERIMENT_NAME or "default"

    if AUTO_SAVE:
        exp_db = get_experiment_db()
        
        # 用户没有指定名称时，优先用预设名；否则按关键配置拼出可读名称。
        # 可读名称方便查看，数据库仍以配置内容的哈希识别是否为同一配置。
        if not EXPERIMENT_NAME:
            if args and hasattr(args, 'preset') and args.preset:
                config_name = args.preset  # directly use preset name
            else:
                # 名称固定包含数据集、求解预算、功能开关、存储方式和模型。
                parts = []
                
                # 0. dataset (fixed position 0) - ✅新增
                parts.append(TPKGConfig.DATASET.lower())  # timequestions 或 multitq
                
                # 1. retries (fixed position 1)
                parts.append(f"retry{TPKGConfig.MAX_RETRIES}")
                
                # 2. decomposition depth (fixed position 2)
                parts.append(f"depth{TPKGConfig.MAX_DEPTH}")
                
                # 3. branch number (fixed position 3)
                parts.append(f"branch{TPKGConfig.MAX_TOTAL_BRANCHES}")
                
                # 4. feature switches (fixed position 4-7)
                if TPKGConfig.USE_HYBRID_RETRIEVAL:
                    parts.append("hybrid")
                else:
                    parts.append("nohybrid")
                    
                # experience pool and template learning have been removed
                    
                if TPKGConfig.USE_UNIFIED_KNOWLEDGE_STORE:
                    parts.append("unified")
                else:
                    parts.append("nounified")
                
                # 5. storage mode (fixed position 8)
                if hasattr(TPKGConfig, 'STORAGE_MODE') and TPKGConfig.STORAGE_MODE:
                    parts.append(TPKGConfig.STORAGE_MODE)
                else:
                    parts.append("shared")
                
                # 6. shared fallback (fixed position 9)
                if hasattr(TPKGConfig, 'ENABLE_SHARED_FALLBACK') and TPKGConfig.ENABLE_SHARED_FALLBACK:
                    parts.append("fallback")
                else:
                    parts.append("nofallback")
                
                # 7. default model (fixed position 10)
                default_model = TPKGConfig.DEFAULT_LLM_MODEL
                # simplify model name
                if 'gpt-4o-mini' in default_model:
                    parts.append("gpt4mini")
                elif 'deepseek-v3' in default_model:
                    parts.append("deepseek")
                elif 'gpt-4' in default_model:
                    parts.append("gpt4")
                else:
                    # for other models, take the first 8 characters
                    model_short = default_model.replace('-', '').replace('_', '')[:8]
                    parts.append(model_short)
                
                # 处理题数和起始偏移不进入可读名称；完整配置仍传给下方的数据库方法。
                
                config_name = "_".join(parts)
        
        # 已有相同配置则复用 config_id；否则创建新的配置记录。
        config_id = exp_db.get_or_create_config(
            config_name=config_name,
            config=TPKGConfig.get_all_config(),
            description=EXPERIMENT_DESC,
            tags=EXPERIMENT_TAGS
        )

    # 在正式处理前打印本轮实际生效的关键参数，便于核对复现实验设置。
    print("\n" + "=" * 80)
    print("current running configuration:")
    print("=" * 80)
    if config_id:
        print(f"configuration ID: {config_id}")
        print(f"configuration name: {config_name}")
        print("-" * 80)
    print(f"maximum retries: {TPKGConfig.MAX_RETRIES}")
    print(f"maximum depth: {TPKGConfig.MAX_DEPTH}")
    print(f"maximum branch number: {TPKGConfig.MAX_TOTAL_BRANCHES}")
    print(f"LLM model: {TPKGConfig.DEFAULT_LLM_MODEL}")
    print(f"use hybrid retrieval: {TPKGConfig.USE_HYBRID_RETRIEVAL}")
    # experience pool and template learning have been removed
    print(f"use unified knowledge store: {TPKGConfig.USE_UNIFIED_KNOWLEDGE_STORE}")
    print(f"process question number: {TPKGConfig.MAX_QUESTIONS}")
    print(f"skip question number: {TPKGConfig.SKIP_QUESTIONS}")
    if TPKGConfig.FILTER_QUESTION_TYPE:
        print(f"filter question type: {TPKGConfig.FILTER_QUESTION_TYPE}")
    print("=" * 80 + "\n")

    # 读取预处理后的题目及字段名：不同数据集的问题文本、ID 字段名称可能不同。
    # t_e 是候选主题实体字段名；候选实体是后续图谱检索的起点。
    datas, q_name, Qid, t_e = load_questions()

    # Agent/Registry 作为求解器接口所需对象传入；逐题控制逻辑在 stepwise.py。
    registry = TemplateRegistry()
    agent = Agent(registry)

    # processed_count 包含当前轮已处理及因数据库已有正确答案而跳过的题目。
    # 新求解题目的 success_count/fail_count 受性能监控开关控制；跳过的正确题单独计入成功数。
    processed_count = 0
    type_matched_count = 0
    success_count = 0
    fail_count = 0
    
    skip_end = TPKGConfig.SKIP_QUESTIONS
    process_end = TPKGConfig.SKIP_QUESTIONS + TPKGConfig.MAX_QUESTIONS
    
    # 先按原始顺序切片，再按题型过滤；题型过滤后的数量可能少于 MAX_QUESTIONS。
    for data in tqdm(datas[skip_end:process_end], desc="Processing questions"):
        if processed_count >= TPKGConfig.MAX_QUESTIONS:
            break
            
        question = data[q_name]
        q_id = data[Qid]
        # 候选实体只保留前 MAX_CANDIDATE_ENTITIES 个，控制检索起点数量。
        topic_entities = data.get(t_e) 
        topic_entities = topic_entities[:TPKGConfig.MAX_CANDIDATE_ENTITIES]
        
        # 从原始测试集按 q_id 补查 qtype：预处理后的候选实体文件未必带题型。
        # 这份测试集文件当前会在每题循环中重新读取，理解性能时应注意这项 I/O。
        with open(TPKGConfig.TEST_DATA_PATH, 'r', encoding='utf-8') as f:
            test_data_list = json.load(f)
        
        dataset_config = TPKGConfig.get_dataset_config()
        id_field = dataset_config['question_id_field']
        
        question_type = None
        for test_item in test_data_list:
            if test_item.get(id_field) == q_id:
                # MultiTQ has qtype field, TimeQuestions may not have
                question_type = test_item.get('qtype', None)
                break
        
        # 题型过滤使用数据集的 qtype；MultiTQ 有该字段，其他数据集可能取到 None。
        if TPKGConfig.FILTER_QUESTION_TYPE:
            if question_type != TPKGConfig.FILTER_QUESTION_TYPE:
                continue
            type_matched_count += 1
        
        processed_count += 1
        
        # 开启实验库时，已有正确结果的题目直接跳过，并计入本轮 success_count。
        # 这里按 config_id + q_id 查询，避免不同实验配置间混用历史结果。
        if exp_db and config_id:
            existing_result = exp_db.get_question_result(config_id, str(q_id))
            if existing_result and existing_result.get('is_correct'):
                print(f"\n{'='*100}")
                print(f"⏭️  skip question #{processed_count} | QID={q_id} (already has correct answer)")
                print(f"   saved answer: {existing_result.get('final_answer', '')[:80]}...")
                print('='*100)
                success_count += 1  # count into success number
                continue

        print(f"\n{'='*100}")
        print(f"question #{processed_count} | QID={q_id}")
        print(f"question type: {question_type}")
        print(f"question: {question}")
        if TPKGConfig.FILTER_QUESTION_TYPE:
            print(f"type matched number: {type_matched_count}")
        print('='*100)

        # 监控器记录单题运行信息；其开关也决定下面是否计算 is_correct 和成败计数。
        if TPKGConfig.ENABLE_PERFORMANCE_MONITORING:
            start_question_monitoring(q_id, question, question_type)

        # 打印当前题的关键求解预算，便于与轨迹及异常日志对照。
        print("Using decomposition with configuration:")
        print(f"  - Max retries: {TPKGConfig.MAX_RETRIES}")
        print(f"  - Max depth: {TPKGConfig.MAX_DEPTH}")
        print(f"  - Hybrid retrieval: {TPKGConfig.USE_HYBRID_RETRIEVAL}")
        # experience pool and template learning have been removed
        
        # 单题求解入口：内部完成题型识别、子问题分解、检索、充分性检查和最终答案生成。
        # quid 供求解器按金标准核对；返回 dict 中的 learning 保存验证信息。
        # trajectory 是逐子问题轨迹，answer_path 是最终答案证据路径。
        result = solve_with_decomposition(
            agent, 
            question, 
            topic_entities, 
            quid=q_id,
            max_retries=TPKGConfig.MAX_RETRIES,
            max_depth=TPKGConfig.MAX_DEPTH,
            max_branch=TPKGConfig.MAX_TOTAL_BRANCHES,
            use_hybrid=TPKGConfig.USE_HYBRID_RETRIEVAL,
            storage_mode=TPKGConfig.STORAGE_MODE,
            llm_model=TPKGConfig.DEFAULT_LLM_MODEL,
            # template learning have been removed
        )
        
        # 优先使用求解器的金标准核对结果 learning.verified。
        # 若没有该字段，退化为“求解器成功且答案非空”的运行状态判断，
        # 此时 is_correct 不能等同于金标准准确率。
        is_correct = None
        if TPKGConfig.ENABLE_PERFORMANCE_MONITORING:
            learning_info = result.get('learning', {})
            if 'verified' in learning_info:
                is_correct = learning_info.get('verified', False)
            else:
                is_correct = result.get('ok', False) and result.get('final_answer', '') != ''
            
            final_answer = result.get('final_answer', '')
            answer_path = result.get('answer_path', [])
            end_question_monitoring(is_correct, final_answer, answer_path)
            
            # 本轮统计与监控器共用 is_correct；未开启监控时两项计数不更新。
            if is_correct:
                success_count += 1
            else:
                fail_count += 1
        
        # 每题完成后立即保存，避免长实验中途退出导致已求解题目全部丢失。
        # 这里还要求 is_correct 非 None；当前实现关闭性能监控时不会进入此落库分支。
        if exp_db and config_id and is_correct is not None:
            trajectory = result.get('trajectory', [])
            
                # 从各子问题的候选事实整理证据边，保留来源、分数和原始候选记录。
            evidence_edges = []
            for step_idx, step in enumerate(trajectory):
                step_result = step.get('result', {})
                candidates = step_result.get('candidates', [])
                for cand in candidates:
                    edge = {
                        'head_entity': cand.get('head', cand.get('entity', '')),
                        'relation': cand.get('relation', ''),
                        'tail_entity': cand.get('tail', cand.get('tail_entity', '')),
                        'time_info': cand.get('time', cand.get('date', '')),
                        'score': cand.get('score', cand.get('similarity', None)),
                        'source': cand.get('provenance', {}).get('method', 'unknown'),
                        'is_used': cand.get('entity') == step_result.get('chosen', {}).get('entity'),
                        'step_index': step_idx,
                        'metadata': {'candidate_full': cand}
                    }
                    evidence_edges.append(edge)
            
            # 再追加最终答案路径中的边；与候选边一起存储，便于回看推理依据。
            answer_path = result.get('answer_path', [])
            for path_item in answer_path:
                if isinstance(path_item, dict):
                    edge = {
                        'head_entity': path_item.get('head', path_item.get('entity', '')),
                        'relation': path_item.get('relation', ''),
                        'tail_entity': path_item.get('tail', ''),
                        'time_info': path_item.get('time', path_item.get('date', '')),
                        'score': path_item.get('score', 1.0),
                        'source': 'final_answer_path',
                        'is_used': True,
                        'metadata': {'from_answer_path': True}
                    }
                    evidence_edges.append(edge)
            
            # 比较当前与历史结果：已有正确答案时不以错误结果覆盖。
            should_save = exp_db.should_save_question(config_id, str(q_id), is_correct)
            
            if should_save:
                # 将答案、正确性、逐步轨迹和证据边写到同一题目记录中。
                exp_db.save_best_results(
                    config_id=config_id,
                    qid=str(q_id),
                    question=question,
                    question_type=question_type,
                    final_answer=result.get('final_answer', ''),
                    is_correct=is_correct,
                    trajectory=trajectory,
                    evidence_edges=evidence_edges
                )
                print(f"💾 already saved question {q_id} result")
            else:
                print(f"⏭️ skip question {q_id} (keep previous correct answer)")
    
    print("\n" + "=" * 80)
    print("processing completed statistics:")
    print("=" * 80)
    print(f"total processed questions: {processed_count}")
    if TPKGConfig.FILTER_QUESTION_TYPE:
        print(f"matched type '{TPKGConfig.FILTER_QUESTION_TYPE}': {type_matched_count} questions")
    if TPKGConfig.ENABLE_PERFORMANCE_MONITORING:
        print(f"success: {success_count} questions")
        print(f"failure: {fail_count} questions")
        if processed_count > 0:
            current_success_rate = success_count / processed_count
            print(f"success rate: {current_success_rate * 100:.2f}%")
    print("=" * 80)
    
    # 除逐题记录外，再保存一次本轮实验总计，并读取该配置的累计统计。
    if exp_db and config_id:
        # record this run
        run_id = exp_db.record_run(
            config_id=config_id,
            total_questions=processed_count,
            success_count=success_count,
            fail_count=fail_count
        )
        
        # show configuration statistics
        print(f"\n📊 configuration '{config_name}' statistics:")
        stats = exp_db.get_config_stats(config_id)
        print(f"   - configuration ID: {config_id}")
        print(f"   - total run count: {stats['config']['run_count']}")
        print(f"   - best success rate: {stats['config']['best_success_rate']*100:.2f}%")
        print(f"   - total saved questions: {stats['stats'].get('total_questions', 0) or 0}")
        
        print(f"\n💾 database location: {exp_db.db_path}")
    
    print("\n✅ experiment completed!")


if __name__ == "__main__":
    # 直接执行本文件时没有 run.py 的 argparse 对象，因此使用上面的环境变量分支。
    run_experiment(None)
