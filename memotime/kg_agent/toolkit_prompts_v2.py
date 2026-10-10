#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Toolkit Selection Prompts for Different Question Types (Current efficient sample version)
Provide specialized toolkit selection prompts for different question types and dynamically read examples for learning
"""
import json
import os
from pathlib import Path
from .intelligent_toolkit_selector import IntelligentToolkitSelector
# Template learner removed, using unified knowledge store
from .fixed_prompts import cold_start_toolkit_examples, get_general_examples
def load_examples_file(examples_file_path: str) -> list:
    """Load example content from example file for LLM learning"""
    try:
        if os.path.exists(examples_file_path):
            with open(examples_file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get('examples', [])
    except Exception as e:
        print(f"Failed loading examples_file_path: {examples_file_path} : {e}")
    return []

def get_initial_examples_fallback(template_key: str, max_examples: int = 3) -> str:
    """
    Get initial examples as fallback mechanism
    """
    examples = cold_start_toolkit_examples.get(template_key, [])
    if not examples:
        # If no corresponding type is found, use general as fallback
        examples = get_general_examples
    
    if not examples:
        return "No initial examples available"
    
    formatted_examples = []
    example_num = 1
    for example in examples[:max_examples]:
        examples_text = ""
        examples_text += f"Example {example_num}:\n"
        example_num += 1
        for key, value in example.items():
            examples_text += f"{key}: {value}\n"
        
        examples_text += "\n"
        formatted_examples.append(examples_text)
    
    return "".join(formatted_examples)


def get_prompt_for_question_type(qtype: str, subquestion: str, seed_info: str, 
                                context_info: str, time_hints: dict, 
                                entity_names: list) -> str:
    """为指定时间题型组装工具选择提示，输入子问题、种子、上下文和时间线索。

    先找统一知识库的成功工具选择样例；历史样例不足时补充冷启动样例，
    最后把样例和当前问题交给选择器的 LLM，而不是在这里直接执行 KG 查询。
    """
    ############################## STEP 1: LOAD EXAMPLES FROM TEMPLATE LEARNING ##############################
    enhanced_prompt = ""
    examples = []

    try:
        # 只取 toolkit_selection 类别的相似历史记录，避免混入分解或答案经验。

        from .enhanced_unified_integration import get_toolkit_selection_enhanced

        examples = get_toolkit_selection_enhanced(
            given_subquestion=subquestion,
            topk=10,
            question_type=qtype,
            similarity_threshold=0.1
        )
        
        if examples:
            enhanced_prompt = "### Examples for Reference (Source: {Experience Pool enhanced})\n\n"
            print(f"✅ Get {len(examples)} toolkit selection examples from unified knowledge store")
            # Build enhanced prompt
            enhanced_examples = "## Successful Examples for Toolkit Selection:\n\n"
            for i, example in enumerate(examples, 1):
                enhanced_examples += f"Example {i}:\n"
                enhanced_examples += f"Subquestion: {example['subquestion']}\n"
                enhanced_examples += f"Indicator: {example['indicator']}\n"
                enhanced_examples += f"Seed_info: {example['seed_info']}\n"
                enhanced_examples += f"Toolkit: {example['toolkit']}\n"
                enhanced_examples += f"Parameters: {example['parameters']}\n"
                enhanced_examples += f"Context: \n"
                enhanced_examples += f"Time_hints: {example['time_hints']}\n"
                enhanced_examples += f"Reasoning: {example['reasoning']}\n\n"
            
            enhanced_prompt = enhanced_examples
    except Exception as e:
        print(f"⚠️ Toolkit selection template learning enhanced failed: {e}")

    # 保留不同题型到提示样例类别的名称映射；当前实际模板键由 qtype 给出。
    template_name = {
        "WHEN": "equal",
        "AFTER": "after_first", 
        "BEFORE": "before_last",
        "BETWEEN": "during_between",
        "SAME_DAY": "equal",
        "SAME_MONTH": "equal",
        "DIRECT": "direct_connection",
        "TIMELINE": "timeline",
        "FIRST_AFTER": "after_first",
        "LAST_BEFORE": "before_last",
        "FIRST_LAST": "first_last",  
        "GENERAL": "general"
    }
    
    template_key = qtype
    
    # # Use template learning examples first
    # examples_text = ""
    # # First layer: try template learning examples
    # try:
    #     template_learner = get_template_learner()
    #     base_prompt = ""
    #     enhanced_prompt = template_learner.get_enhanced_prompt_for_task(
    #         'toolkit_selection', template_key, subquestion, base_prompt, 3
    #     )
        
    #     if enhanced_prompt and "## Successful Examples" in enhanced_prompt:
    #         examples_text = enhanced_prompt
    #         examples_source = "template_learning"
    #         print("✅ Template learning examples found and used")
    #     else:
    #         print("⚠️ Template learning available but no examples found")
            
    # except Exception as e:
    #     print(f"⚠️ Template learning failed: {e}")



    # # If template learning has no examples, fallback to file examples
    # if not examples_text:
    #     # Use relative path from current file location
    #     template_dir = Path(__file__).parent.parent / 'templates'
    #     examples_file = str(template_dir / f'{template_key}_examples.json')
    #     examples_list = load_examples_file(examples_file)
        
    #     if examples_list:
    #         examples_parts = []
    #         for example in examples_list[:3]:  # 前3个示例以避免过于冗长
    #             examples_parts.append(
    #                 f"————————————————————————————————————————————————————————————————————————————————\n"
    #                 f"Subquestion: {example.get('subquestion', '')}\n"
    #                 f"Indicator: {example.get('indicator', '')}\n"
    #                 f"seed_info: {example.get('seeds', [])}\n"
    #                 f"Selected Toolkit: {example.get('toolkit', '')}\n"
    #                 f"Context: {example.get('context', '')}\n"
    #                 f"Time Hints: {example.get('time_hints', '')}\n"
    #                 f"————————————————————————————————————————————————————————————————————————————————\n"
    #             )
    #         examples_text = "\n".join(examples_parts)
    # Third layer: use initial examples library (cold start solution)
    # if not examples_text:
    # 当前分支在无经验或不足 10 条时加载冷启动样例；即使有少量历史样例也会执行。
    # 若历史样例达到 10 条，下面未给 examples_text 赋值，后续格式化可能失败。
    if len(enhanced_prompt) == 0 or len(examples) < 10:
        # print(f"📚 No historical examples found, using initial examples for question type: {qtype}")
        examples_text = get_initial_examples_fallback(template_key, max_examples=10)
        examples_source = "initial_examples"
        
        if examples_text and "No initial examples available" not in examples_text:
            print(f"✅ Initial examples loaded for question type: {qtype}")
        else:
            print(f"⚠️ No initial examples available for question type: {qtype}")
            # Fourth layer: use general examples as final fallback
            examples_text = get_initial_examples_fallback("general", max_examples=2)
            examples_source = "general_fallback"
            print("🔄 Using general examples as final fallback")

    ############################## Build final prompt ##############################
    
    core_prompt = f"""You are an expert intelligent toolkit selector specialising in temporal knowledge graphs.
    
### Target and Requirements
1. Please accurately analyze the question based on the provided examples and toolkit description
2. Automatically extract Question parsing and seed settings           
3. Combine Question parsing and time constraints for optimal toolkit configuration
4. OUTPUT should strictly require JSON standard format

### My Question
Question: {subquestion}
entity_names: {entity_names}     
seed_info: {seed_info}
context_info: {context_info}
time_hints: {time_hints}


{enhanced_prompt}
### Examples for Reference (Source: {examples_source})
{examples_text}                

### available toolkits and parameters
• OneHop — entity,direction,after,before,between,same_day,same_month,sort_by_time,limit(default:100)  
• AfterFirst — entity,after,limit(100)                
• BeforeLast — entity,before,limit(100)             
• DirectConnection — entity1,entity2,direction,limit(200),after,before,between,same_day,same_month,sort_by_time
• Timeline — entity,direction,sort_by_time(true),after,before,limit(100)             
• BetweenRange — entity,between,limit(50)
• DayEvents — date (specific_day)          
• MonthEvents — month (specific_month)
• YearEvents — year (specific_year)

### Output JSON only:
{{
  "selected_toolkits": [
    {{ "original_name":"ToolkitName", "parameters":{{"entity1":"EntityName","entity2":"CountryName","direction":"both","limit":200}}, "reasoning":"Reason statement","priority":1}}
   ]
}}

"""

    # # When encountering FIRST_LAST type, add intelligent judgment logic
    # if qtype == "FIRST_LAST":
    #     # Analyze the question, decide to use AfterFirst or BeforeLast examples
    #     if "last" in subquestion.lower():
    #         template_key = "before_last"
    #         suggested_toolkit = "BeforeLast"
    #         analysis_logic = "Last event uses BeforeLast, time set to latest year 2025-01-01"
    #     elif "first" in subquestion.lower():
    #         template_key = "after_first"  
    #         suggested_toolkit = "AfterFirst"
    #         analysis_logic = "First event uses AfterFirst, time set to earliest year 1900-01-01"
    
    #     # Add routing suggestion in prompt
    #     core_prompt += f"""
    # ### Smart Routing Suggestion
    # For this FIRST_LAST question, consider using: {suggested_toolkit}
    # Reasoning: {analysis_logic}
    # """

# Add toolkit detailed description
#     for toolkit_example in IntelligentToolkitSelector().toolkit_examples:
#         core_prompt += f"""
# {toolkit_example.name}
# {toolkit_example.description}
# {toolkit_example.example_call}
# """
    # print("-"*50)
    # print("Question:", subquestion)
    # print("Qtype:", qtype)
    # print("Core Prompt:", core_prompt)
    # exit()
    return core_prompt

# Keep the original test function
def test_parse_examples():
    """Test example reading function"""
    example_files = [
        "after_first_examples.json",
        "before_last_examples.json", 
        "equal_examples.json",
        "direct_connection_examples.json",
        "during_between_examples.json",
        "timeline_examples.json",
        "first_last_examples.json",
    ]
    
    # Use relative path from current file location
    template_dir = Path(__file__).parent.parent / 'templates'
    
    all_loaded = []
    for file in example_files:
        path_example = str(template_dir / file)
        resulter = load_examples_file(path_example)
        all_loaded.append((file, len(resulter) ))
    print(f"[INFO] Parsable examples loaded: {all_loaded}")
    return all_loaded

def test_template_learning_integration():
    """Test template learning integration function"""
    print("Test template learning integration...")
    
    # Test with template learning data
    qtype = 'AFTER'
    subquestion = 'When did Tony Blair express an interest in cooperating with Iraq?'
    seed_info = 'Tony Blair, Iraq'
    context_info = 'After first type question'
    time_hints = {'after': '2006-01-01'}
    entity_names = ['Tony Blair', 'Iraq']
    
    prompt = get_prompt_for_question_type(qtype, subquestion, seed_info, context_info, time_hints, entity_names)
    
    # Check if examples are included
    if "No initial examples available" in prompt:
        print("❌ No examples found")
    elif "Examples for Reference" in prompt:
        print("✅ Examples successfully integrated")
        
        # Check example source
        if "Source: initial_examples" in prompt:
            print("📚 Using initial examples (cold start solution)")
        elif "Source: template_learning" in prompt:
            print("🧠 Using template learning examples")
        elif "Source: file_system" in prompt:
            print("📁 Using file system examples")
        elif "Source: general_fallback" in prompt:
            print("🔄 Using general fallback examples")
    else:
        print("❌ Examples integration failed")
    
    return True

if __name__ == "__main__":
    # Quick test to ensure prompt generation is correct
    test_parse_examples()
    test_template_learning_integration()
