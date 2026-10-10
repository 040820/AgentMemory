#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================
# file: Data/prepare_datasets.py
# =============================
# 数据准备顺序：
# 1. 原始 KG TSV → SQLite 的 entities/edges 表；实体 ID 在建库时生成。
# 2. 实体名 → BGE-M3 向量及 FAISS HNSW 索引，供关键词匹配实体使用。
# 3. test.json 中每道题 → 关键词 → 相似实体 → 带 candidates 的新 JSON。
# 4. 可选：为运行期的混合检索建事实索引、为关系建嵌入缓存。
# 本文件负责调度；关键词提取在 entity_try.py，语义打分在 new.py。
# memotime/data_process.py 会读取第 3 步产物供单题求解器使用。


import os
import sys
import json
import sqlite3
import pickle
import asyncio
import ast
from datetime import datetime
from typing import List, Tuple, Dict, Any
from tqdm import tqdm

# Windows 控制台可能用 GBK；将标准输出/错误设为 UTF-8 以打印进度符号。
# 如果当前输出流不支持 reconfigure，则沿用原来的编码设置。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 把项目根目录和 Data 目录放到模块搜索路径，分别用于 memotime 与同目录辅助模块。
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))  # Add project root for memotime module
sys.path.insert(0, str(Path(__file__).parent))

# 尝试加载项目根目录 .env；关键词抽取调用可从环境变量读取模型配置。
# 加载失败不会阻止脚本启动，后续调用仍可能因缺少配置而失败。
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except Exception:
    pass
try:
    # new.py 负责实体索引和按关键词召回；entity_try.py 负责从问题抽关键词。
    from new import cmd_build_index, semantic_candidates_by_keywords
    from entity_try import combined_keywords
except ImportError as e:
    # 这里只打印警告；后续使用缺失的函数时仍会进入相应的异常处理。
    print("Warning: Could not import helper modules, will use alternative methods...")

# 混合事实索引是可选步骤；缺少任一依赖时保留前三步的处理能力。
try:
    import numpy as np
    import torch
    import faiss
    from FlagEmbedding import BGEM3FlagModel
    HYBRID_AVAILABLE = True
except ImportError as e:
    print(f"Warning: Hybrid retrieval dependencies missing: {e}")
    print("Will skip hybrid index build...")
    HYBRID_AVAILABLE = False

# 两个数据集共用处理流程，但 TSV 列数、JSON 字段名和输出目录不同。
# kg_format 只控制建库时读哪几列；questions_file 是生成候选的输入题目文件。
DATASETS = {
    "TimeQuestions": {
        "root": str(Path(__file__).parent / "TimeQuestions"),
        "kg_file": "full.txt",
        "questions_file": "test.json",
        "kg_format": "5cols",  # 原始列：主语、关系、宾语、开始、结束；当前建库逻辑只取前四列。
        "question_id_field": "Id",
        "question_text_field": "Question",
        "answer_field": "Answer",
        "hybrid_cache_dir": str(Path(__file__).parent / "TimeQuestions" / "hybrid_cache"),
        "embedding_cache_dir": str(Path(__file__).parent / "TimeQuestions" / "embedding_cache_timequestions")
    },
    "MultiTQ": {
        "root": str(Path(__file__).parent / "MultiTQ"),
        "kg_file": "full_fixed.txt",
        "questions_file": "test.json",
        "kg_format": "4cols",  # 原始列：主语、关系、宾语、时间。
        "question_id_field": "quid",
        "question_text_field": "question",
        "answer_field": "answers",
        "hybrid_cache_dir": str(Path(__file__).parent / "MultiTQ" / "hybrid_cache"),
        "embedding_cache_dir": str(Path(__file__).parent / "MultiTQ" / "embedding_cache_multitq")
    }
}


class DatasetPreprocessor:
    """封装单个数据集的路径、知识图谱建库、实体候选生成及可选缓存。"""
    
    def __init__(self, dataset_name: str):
        """按数据集名称配置输入、数据库、索引与输出路径。

        Args:
            dataset_name: DATASETS 中的键，只接受 ``TimeQuestions`` 或 ``MultiTQ``。

        Raises:
            ValueError: 数据集名称不在 DATASETS 中。
        """
        # 先校验名称，避免对不存在的数据集拼接路径或创建缓存目录。
        if dataset_name not in DATASETS:
            raise ValueError(f"Unknown Dataset: {dataset_name}. Supported Datasets: {list(DATASETS.keys())}")
        
        # 保留配置字典，后续按数据集读取题目字段名和 KG 文件格式。
        self.dataset_name = dataset_name
        self.config = DATASETS[dataset_name]
        
        # root 是该数据集目录；其余路径分别指原始 KG、问题、SQLite 和实体索引。
        self.root = self.config["root"]
        self.kg_file = os.path.join(self.root, self.config["kg_file"])
        self.questions_file = os.path.join(self.root, self.config["questions_file"])
        self.db_path = os.path.join(self.root, f"tempkg_{dataset_name.lower()}.db")
        self.index_dir = os.path.join(self.root, f"entity_index_{dataset_name.lower()}")
        # 第三步的产物保留原题全部字段，并追加 keywords_used 与 candidates。
        self.output_file = os.path.join(self.root, f"questions_with_candidates_{dataset_name.lower()}.json")
        # 以下目录对应可选的事实混合索引与关系嵌入缓存。
        self.hybrid_cache_dir = self.config.get("hybrid_cache_dir", 
                                                 os.path.join(self.root, "hybrid_cache"))
        self.embedding_cache_dir = self.config.get("embedding_cache_dir",
                                                     os.path.join(self.root, f"embedding_cache_{dataset_name.lower()}"))
        
        # 只创建缓存目录；实体索引目录由 new.cmd_build_index 在需要时创建。
        os.makedirs(self.hybrid_cache_dir, exist_ok=True)
        os.makedirs(self.embedding_cache_dir, exist_ok=True)
        
        # 启动时打印路径，便于确认当前处理的是哪个数据集及产物位置。
        print(f"\n{'='*80}")
        print(f"Initializing {dataset_name} dataset preprocessor")
        print(f"{'='*80}")
        print(f"KGFile: {self.kg_file}")
        print(f"questionFile: {self.questions_file}")
        print(f"Database: {self.db_path}")
        print(f"indexDir: {self.index_dir}")
        print(f"OutputFile: {self.output_file}")
        print(f"Hybridcache: {self.hybrid_cache_dir}")
    
    def _parse_time(self, time_str: str) -> int:
        """将年份或日期字符串解析为整数年份；当前主建库流程未调用此函数。

        Args:
            time_str: 如 ``2005``、``2005-01-01``；空值或特殊值可能无法解析。
        Returns:
            年份整数；解析失败返回 None。
        """
        # 空字符串没有可用年份。
        if not time_str or not time_str.strip():
            return None
        
        time_str = time_str.strip()
        
        try:
            # 日期只取第一个连字符前的年份，不保留月日。
            if '-' in time_str:
                year_str = time_str.split('-')[0]
                return int(year_str)
            
            # 纯数字直接转为年份。
            if time_str.isdigit():
                return int(time_str)
            
            # 以 + 开头的特殊占位时间不参与年份匹配。
            if time_str.startswith('+'):
                return None
            
            # 其他形式尝试按整数解析；失败统一返回 None。
            return int(time_str)
        except:
            return None
    
    def _parse_time_to_timestamp(self, time_str: str) -> int:
        """把原始时间转为 Unix 秒时间戳，供 SQLite 时间排序与筛选。

        Args:
            time_str: ``YYYY-MM-DD``、``YYYY-MM`` 或纯 ``YYYY`` 字符串。
        Returns:
            对应日期的时间戳；仅有年份时取该年 1 月 1 日，失败返回 None。
        """
        # 空时间不能用于比较；strip 去除 TSV 字段两端空格。
        if not time_str or not time_str.strip():
            return None
        
        time_str = time_str.strip()
        
        try:
            if '-' in time_str:
                # 按连字符拆成年月日；缺失日时取每月 1 日。
                parts = time_str.split('-')
                if len(parts) >= 3:
                    year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
                elif len(parts) == 2:
                    year, month, day = int(parts[0]), int(parts[1]), 1
                else:
                    year, month, day = int(parts[0]), 1, 1
                
                # datetime.timestamp() 对无时区对象使用运行机器的本地时区。
                dt = datetime(year, month, day)
                return int(dt.timestamp())
            elif time_str.isdigit():
                # 纯年份按该年第一天计算时间戳。
                year = int(time_str)
                dt = datetime(year, 1, 1)
                return int(dt.timestamp())
            else:
                return None
        except:
            return None
    
    def create_database(self, force_rebuild: bool = False):
        """将原始 KG 的制表符分隔事实写入实体表和带时间的边表。

        Args:
            force_rebuild: 为 True 时删除已有 SQLite 文件并重新建库；默认复用已有库。
        Returns:
            无返回值；输入缺失时打印原因并提前结束。
        """
        print(f"\n{'='*80}")
        print(f"Step 1: Create SQLite database ({self.dataset_name})")
        print(f"{'='*80}")
        
        # 默认复用已有数据库；先检查关键 edges 表，缺失时视为无效库并重建。
        if os.path.exists(self.db_path) and not force_rebuild:
            print(f"✅ Database exists: {self.db_path}")
            # Validate table structure
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='edges'")
            has_edges = cur.fetchone() is not None
            conn.close()
            
            if not has_edges:
                print(f"⚠️ Database table structure incorrect (missing edges table), rebuilding...")
                os.remove(self.db_path)
            else:
                return
        
        # 强制重建会删除旧数据库；该行为由显式 --force-rebuild-db 参数触发。
        if force_rebuild and os.path.exists(self.db_path):
            print(f"🔄 Force rebuild: removing existing database...")
            os.remove(self.db_path)
        
        # 找不到原始 KG 文件时不能继续生成实体和事实表。
        if not os.path.exists(self.kg_file):
            print(f"❌ KG file does not exist: {self.kg_file}")
            return
        
        
        # WAL 与 NORMAL 降低批量写入开销；外键确保边引用的实体先入库。
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("PRAGMA foreign_keys=ON")
        
        cur = conn.cursor()
        # entities 存实体名及 ID；edges 存关系、原始时间字符串和可比较的时间戳。
        # UNIQUE 约束结合后面的 INSERT OR IGNORE 去除重复事实。
        cur.executescript("""
            CREATE TABLE IF NOT EXISTS entities(
                id   INTEGER PRIMARY KEY,
                name TEXT NOT NULL UNIQUE
            );
            CREATE TABLE IF NOT EXISTS edges(
                id            INTEGER PRIMARY KEY,
                head_id       INTEGER NOT NULL,
                relation      TEXT NOT NULL,
                tail_id       INTEGER NOT NULL,
                t_start       TEXT NOT NULL,
                t_end         TEXT NOT NULL,
                t_start_epoch INTEGER NOT NULL,
                t_end_epoch   INTEGER NOT NULL,
                granularity   TEXT NOT NULL DEFAULT 'year',
                FOREIGN KEY(head_id) REFERENCES entities(id),
                FOREIGN KEY(tail_id) REFERENCES entities(id),
                UNIQUE(head_id, relation, tail_id, t_start, t_end)
            );
            CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name);
            CREATE INDEX IF NOT EXISTS idx_edges_head ON edges(head_id);
            CREATE INDEX IF NOT EXISTS idx_edges_tail ON edges(tail_id);
            CREATE INDEX IF NOT EXISTS idx_edges_tstart ON edges(t_start_epoch);
            CREATE INDEX IF NOT EXISTS idx_edges_tend ON edges(t_end_epoch);
            CREATE INDEX IF NOT EXISTS idx_edges_tstart_str ON edges(t_start);
        """)
        
        print("📊 Parsing KG file and inserting data...")
        
        # 用字典给每个出现过的实体名分配 ID；边暂存在列表中，待实体入库后批量写入。
        entities = {}  # name -> id
        edges_buffer = []
        
        # 每一行是一条 KG 事实。空行跳过，非空行按制表符拆列。
        with open(self.kg_file, 'r', encoding='utf-8') as f:
            for line in tqdm(f, desc="Reading KG"):
                line = line.strip()
                if not line:
                    continue
                
                parts = line.split('\t')
                
                # TimeQuestions 原始文件有五列，但当前实现只取开始时间，忽略结束时间。
                # MultiTQ 的四列直接全部取用。
                if self.config["kg_format"] == "5cols":
                    if len(parts) >= 5:
                        head, relation, tail, time = parts[0], parts[1], parts[2], parts[3]
                elif self.config["kg_format"] == "4cols":
                    if len(parts) >= 4:
                        head, relation, tail, time = parts[:4]
                else:
                    continue
                
                # 删除字段两端空格；实体名和关系名本身不做额外规范化。
                head, relation, tail, time = head.strip(), relation.strip(), tail.strip(), time.strip()
                
                # 主语、关系、宾语或时间为空的事实不写入图谱。
                if not all([head, relation, tail, time]):
                    continue
                
                # 同名实体复用 ID；ID 从 1 开始按首次遇见顺序递增。
                if head not in entities:
                    entities[head] = len(entities) + 1
                if tail not in entities:
                    entities[tail] = len(entities) + 1
                
                # 为日期或纯年份生成可比较的秒时间戳；解析失败会用 0 作为占位。
                time_epoch = self._parse_time_to_timestamp(time)
                if time_epoch is None:
                    time_epoch = 0
                
                # t_start/t_end 都写入同一原始时间；此处没有保留 5 列输入的结束时间。
                # granularity 只按字符串是否含连字符区分 day/year。
                edges_buffer.append((
                    entities[head], relation, entities[tail],
                    time, time, time_epoch, time_epoch, 'day' if '-' in time else 'year'
                ))
        
        # 外键已开启，故先插入实体表，再插入引用这些 ID 的边表。
        print(f"💾 Inserting {len(entities)} entities...")
        for name, eid in tqdm(entities.items(), desc="Inserting entities"):
            cur.execute("INSERT INTO entities (id, name) VALUES (?, ?)", (eid, name))
        conn.commit()
        
        # 每 50,000 条边为一批，INSERT OR IGNORE 跳过 UNIQUE 约束下的重复事实。
        print(f"💾 Inserting edge data (batch processing)...")
        batch_size_insert = 50000
        for i in tqdm(range(0, len(edges_buffer), batch_size_insert), desc="Batch inserting edges"):
            batch = edges_buffer[i:i+batch_size_insert]
            cur.executemany(
                "INSERT OR IGNORE INTO edges(head_id, relation, tail_id, t_start, t_end, t_start_epoch, t_end_epoch, granularity) VALUES (?,?,?,?,?,?,?,?)",
                batch
            )
            conn.commit()
        
        # 实际边数可能小于缓冲区长度，因为重复边被忽略。
        cur.execute("SELECT COUNT(*) FROM edges")
        n_edges = cur.fetchone()[0]
        
        conn.commit()
        conn.close()
        
        print(f"✅ Database creation completed: {self.db_path}")
        print(f"   - Number of entities: {len(entities)}")
        print(f"   - Number of edges: {n_edges}")
        print(f"   - Time storage format:")
        print(f"     • t_start: Original string (YYYY-MM-DD for MultiTQ, YYYY for TimeQuestions)")
        print(f"     • t_start_epoch: Year as integer (for backward compatibility)")
        print(f"   📝 Note: Use t_start string for precise date comparison")
    
    def build_entity_index(self, entity_ids=None):
        """从 SQLite 实体表构建实体名向量和 HNSW 近邻索引。

        Args:
            entity_ids: 仅索引这些实体 ID；None 表示索引全表。小样本模式会传入子集。
        Returns:
            无返回值；已有索引或数据库缺失时提前结束。
        """
        print(f"\n{'='*80}")
        print(f"Step 2: Build entity index ({self.dataset_name})")
        print(f"{'='*80}")

        # 只以 HNSW 文件是否存在判断索引已建；不会核对它是否对应本次 entity_ids。
        if os.path.exists(os.path.join(self.index_dir, "entity_hnsw.index")):
            print(f"✅ Index already exists: {self.index_dir}")
            return

        # new.cmd_build_index 需要从 SQLite entities 表读取实体 ID 和名称。
        if not os.path.exists(self.db_path):
            print(f"❌ Database does not exist: {self.db_path}")
            return

        print(f"🔨 Building entity index to: {self.index_dir}")

        try:
            # new.py 将实体名规范化、调用本地 BGE-M3 编码并保存向量/ID 映射。
            # M、ef_construction、ef_search 是 HNSW 的图连接和搜索参数。
            cmd_build_index(
                db_path=self.db_path,
                out_dir=self.index_dir,
                fp16=False,
                build_hnsw=True,
                M=64,
                ef_construction=400,
                ef_search=128,
                entity_ids=entity_ids
            )
            print("✅ Entity index build completed")
        except Exception as e:
            print(f"❌ Index build failed: {e}")
            print("   Skipping index build")

    def _parse_answer_labels(self, answer_value) -> List[str]:
        """从 TimeQuestions 的 Answer 字段提取实体答案标签，供子集建索引。

        Args:
            answer_value: 可按 Python 字面量解析的字符串、字典或列表。
        Returns:
            所有 AnswerType 为 Entity 的 WikidataLabel；无效输入返回空列表。
        """
        # 此函数不直接生成 candidates；只帮助 -n 模式选择被编码的实体范围。
        if not answer_value:
            return []
        try:
            # Answer 可能是字符串形式的列表/字典；literal_eval 不执行任意表达式。
            items = ast.literal_eval(str(answer_value))
        except Exception:
            return []
        # 把单个字典统一成列表，后面只处理 Entity 类型的答案。
        if isinstance(items, dict):
            items = [items]
        labels = []
        if isinstance(items, list):
            for it in items:
                if isinstance(it, dict) and it.get("AnswerType") == "Entity":
                    lab = it.get("WikidataLabel")
                    if lab:
                        labels.append(str(lab))
        return labels

    def _relevant_entity_ids(self, questions, k_hop: int = 2) -> set:
        """从给定题目收集小样本索引需要覆盖的实体 ID。

        Args:
            questions: 原始题目字典列表；字段名按当前数据集配置读取。
            k_hop: 传给 extract_3hop_subgraph 的扩展轮数；主入口传 0 时，
                辅助函数仍会取种子相邻的边，因此返回值包含一跳邻居。
        Returns:
            种子及其邻边中的实体 ID 集合；无种子或导入失败返回 None。

        注意：此处把题目的答案标签加入索引范围，属于使用金标准缩小候选空间。
        它只服务于 -n 子集索引，不是正常全量索引时的候选打分步骤。
        """
        try:
            from entity_try import literal_hit_entities, extract_3hop_subgraph
        except ImportError as e:
            print(f"⚠️ cannot import entity helpers: {e}")
            return None

        # 不同数据集的问题文本/答案字段名不同，统一从 DATASETS 读取。
        qtext_field = self.config["question_text_field"]
        ans_field = self.config["answer_field"]

        # set 去重；一个连接复用整批题目查询答案标签，避免每题重新建连接。
        seeds = set()
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        for q in questions:
            # 第一类种子：题面上能与 KG 实体名直接匹配的实体。
            try:
                for eid, _name in literal_hit_entities(self.db_path, q.get(qtext_field, "")):
                    seeds.add(int(eid))
            except Exception:
                pass
            # 第二类种子：可解析出的实体答案标签，按不区分大小写的名称查 ID。
            # 对不使用 AnswerType/WikidataLabel 结构的数据集，该列表通常为空。
            for label in self._parse_answer_labels(q.get(ans_field)):
                cur.execute("SELECT id FROM entities WHERE lower(name)=lower(?)", (label,))
                seeds.update(int(r[0]) for r in cur.fetchall())
        conn.close()

        # 连一个种子都没有时，调用方将回退到全量实体索引。
        if not seeds:
            return None

        # 基于种子取邻边并收集边两端 ID，为局部实体检索保留上下文。
        ids = set(seeds)
        try:
            # 辅助函数的四元组是 (head_id, relation, tail_id, t_start)。
            for h, _hn, t, _tn in extract_3hop_subgraph(self.db_path, seeds, k=k_hop):
                ids.add(int(h))
                ids.add(int(t))
        except Exception as e:
            print(f"⚠️ subgraph expand failed: {e}")
        return ids
    
    def generate_candidates(self, num_questions: int = None):
        """为测试集每道题生成可供求解器挑选的候选主题实体。

        Args:
            num_questions: 只处理 test.json 前 N 题；None 或 0 表示处理全部题目。
        Returns:
            无返回值；输出到 ``questions_with_candidates_<dataset>.json``。

        主链：题目文本 → combined_keywords → semantic_candidates_by_keywords
        → score > 0.65 → [{id, name, score}]。这里不拿金标准答案直接打分；
        但 -n 模式可在前面的子集建索引步骤用答案标签限定索引范围。
        候选实体是后续检索的起点，不保证就是最终答案。
        """
        print(f"\n{'='*80}")
        print(f"Step 3: Generate candidate entities ({self.dataset_name})")
        print(f"{'='*80}")
        
        # 缺题目文件无法逐题抽关键词；缺 SQLite 则无法取得实体名映射。
        if not os.path.exists(self.questions_file):
            print(f"❌ Question file does not exist: {self.questions_file}")
            return
        
        if not os.path.exists(self.db_path):
            print(f"❌ Database does not exist: {self.db_path}")
            return
        
        # test.json 是原始测试题列表；整个列表一次读入内存。
        print(f"📁 Reading question file: {self.questions_file}")
        with open(self.questions_file, 'r', encoding='utf-8') as f:
            questions_all = json.load(f)
        
        # -n 仅截取文件开头的题目，保持它们在 test.json 中的原始顺序。
        total_available = len(questions_all)  # 用于输出“前 N 题 / 总题数”统计。
        if num_questions:
            questions = questions_all[:num_questions]  # 不随机采样，直接切片。
            print(f"📊 Processing first {num_questions} questions (out of {total_available} total)")
            print(f"   💡 Tip: Use --num-questions N or -n N to control the number of questions")
        else:
            questions = questions_all  # 不限制题数时复用完整列表。
            print(f"📊 Processing all {len(questions)} questions")
            print(f"   💡 Tip: Use --num-questions N or -n N to process fewer questions for testing")
        
        # 用 entities 表建立 ID → 名称映射；向量召回返回 ID 后据此写 name。
        conn = sqlite3.connect(self.db_path)  # 连接本数据集建好的图数据库。
        cur = conn.cursor()  # 创建执行 SQL 的游标。
        cur.execute("SELECT id, name FROM entities")  # 读出全部实体 ID 与名称。
        id_name = {int(eid): nm for eid, nm in cur.fetchall()}  # 转为便于查名的字典。
        conn.close()  # 名称映射已在内存中，及时关闭数据库连接。

        # topk：每题最多返回 200 个已排序候选；per_kw_topk：每个关键词的初筛上限。
        # agg=max：同一实体匹配多个关键词时，取最相似的关键词分数。
        # 具体向量归一化、FAISS 召回、精确打分与名称加分发生在 new.py。
        output_data = []  # 汇集最终写回 JSON 的题目对象。
        topk = 200  # 一道题最多保留语义召回前 200 个实体。
        per_kw_topk = 500  # 一个关键词在初筛阶段最多取 500 个实体。
        agg = "max"  # 多关键词得分聚合方式：取该实体最高的关键词相似度。

        # 两个数据集的 ID、文本和答案字段名不同；字段名由 DATASETS 配置提供。
        question_id_field = self.config["question_id_field"]  # 如 Id / quid。
        question_text_field = self.config["question_text_field"]  # 如 Question / question。
        # 此处虽取出答案字段名，generate_candidates 后续没有使用它参与打分。
        answer_field = self.config["answer_field"]  # 当前函数没有再引用该变量。

        # tqdm 只显示进度，不改变逐题处理顺序。
        for q in tqdm(questions, desc="Generating candidate entities"):
            # 当前题缺文本时传入空字符串；qid 只用于失败日志和最终原字段保留。
            question_text = q.get(question_text_field, "")  # 取自然语言问题。
            qid = q.get(question_id_field)  # 取数据集自带的题目 ID。
            
            # combined_keywords 先尝试 LLM 抽实体样式的短语，再合并规则提取的短语。
            # 辅助函数会做清洗和去重；这里保存实际用于检索的列表。
            try:
                keywords = combined_keywords(question_text)  # LLM 关键词 + 启发式关键词。
            except Exception as e:
                # 整体抽取异常时，退回按空格切词并丢掉长度 <= 2 的词。
                # 连切词都为空时以整题为一个关键词，保证有检索输入。
                keywords = [w for w in question_text.lower().split() if len(w) > 2]
                if not keywords:
                    keywords = [question_text]  # 空词表时最后回退到原题文本。
            
            # 默认空列表：索引缺失、召回失败或分数都不足时，仍会输出该题。
            candidates = []  # 没有索引或召回异常时保持空列表。
            try:
                # 这里只检查目录是否存在；目录内向量/元数据文件不完整会在调用中报错。
                if os.path.exists(self.index_dir):
                    # new.py 将每个关键词编码，在实体索引取近邻，并对候选重新打分。
                    # exclude_ids=[] 表示不排除已有实体；use_faiss=True 优先用 HNSW。
                    cands = semantic_candidates_by_keywords(
                        db_path=self.db_path,  # 从 entities 表回查召回 ID 对应的名称。
                        index_dir=self.index_dir,  # 读取实体向量、ID 映射和可选 HNSW 文件。
                        keywords=keywords,  # 本题实际抽取的关键词列表。
                        topk=topk,  # 在精确重评分后只返回前 200 个。
                        per_kw_topk=per_kw_topk,  # 每个关键词的近邻初筛上限。
                        agg=agg,  # 多关键词分数取最大值。
                        exclude_ids=[],  # 不排除任何实体 ID。
                        use_faiss=True  # 索引可用时走 FAISS，否则辅助函数回退精确检索。
                    )
                    # cands 的单项为 (实体 ID, 实体名, 分数)；名称以本轮 SQLite 为准。
                    # 严格大于 0.65 才保留，等于 0.65 的候选也会被过滤。
                    # 转为 JSON 可序列化的 int/float，顺序沿用 new.py 的降序结果。
                    candidates = [
                        {"id": int(eid), "name": id_name.get(int(eid), ""), "score": float(score)}
                        for eid, _nm, score in cands if score > 0.65
                    ]
            except Exception as e:
                # 单题候选生成失败时打印题目 ID，继续处理后续题目；本题候选可能为空。
                print(f"Warning: Candidate generation failed for question {qid}: {e}")
            
            # 浅拷贝原题，不改写 test.json；追加两个预处理字段供下游使用。
            result = q.copy()  # 原题字段和答案字段原样保留。
            result["keywords_used"] = keywords  # 记录用于召回的关键词，方便诊断实体链接。
            result["candidates"] = candidates  # 记录实体 ID、名称和相似度分数。
            
            # 即使没有候选也写入 output_data，以保持题目 ID 和原始顺序。
            output_data.append(result)  # 输出题目与输入题目一一对应。
        
        # 覆盖写入本数据集的候选 JSON；ensure_ascii=False 保留原始 Unicode 文本。
        print(f"💾 Saving results to: {self.output_file}")
        with open(self.output_file, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)  # 缩进 2 格，便于检查。
        
        print(f"✅ Candidate entity generation completed, processed {len(output_data)} questions")
        
        # 计算每题候选数量的平均值；空题目列表时避免除零。
        total_candidates = sum(len(q["candidates"]) for q in output_data)
        avg_candidates = total_candidates / len(output_data) if output_data else 0
        print(f"   - Average candidates per question: {avg_candidates:.1f}")
        
        # 打印第一题及最多三个候选用于人工检查；不会修改已写出的结果。
        if output_data:
            print(f"\nSample question:")
            example = output_data[0]
            print(f"  Question ID: {example.get(question_id_field)}")
            print(f"  Question: {example.get(question_text_field)}")
            print(f"  Keywords: {example.get('keywords_used', [])}")
            print(f"  Candidate count: {len(example.get('candidates', []))}")
            if example.get('candidates'):
                print(f"  Top 3 candidates:")
                for cand in example['candidates'][:3]:
                    print(f"    - {cand['name']} (score: {cand['score']:.3f})")
    
    def build_hybrid_index(self, gpu_id: int = 0, use_gpu: bool = True):
        """为运行期事实检索构建 BGE-M3 向量及 FAISS IVF 索引。

        该索引编码的是带关系/时间的图谱事实，与候选实体使用的 HNSW 索引不同。
        Args:
            gpu_id: 指定 CUDA 设备编号；缓存文件名不包含此编号。
            use_gpu: 有可用 CUDA 时是否尝试在 GPU 上构建 FAISS 索引。
        Returns:
            无返回值；依赖、数据库或模型缺失时打印信息并结束。
        """
        print(f"\n{'='*80}")
        print(f"Step4: Build Hybrid retrieval index ({self.dataset_name})")
        print(f"{'='*80}")
        
        # 该步骤依赖 numpy、torch、faiss、FlagEmbedding；候选实体步骤不依赖它。
        if not HYBRID_AVAILABLE:
            print("❌ Hybrid retrieval dependency missing, Skip indexBuild")
            print("   Please install: pip install faiss-gpu torch FlagEmbedding")
            return
        
        # 事实索引的数据从刚才建立的 SQLite 边表中提取。
        if not os.path.exists(self.db_path):
            print(f"❌ Database does not exist: {self.db_path}")
            print("   Please run create_database()")
            return
        
        # 三份缓存分别保存索引、原始事实文本与其向量，路径与 GPU 编号无关。
        index_path = os.path.join(self.hybrid_cache_dir, "hybrid_index.bin")
        data_cache_path = os.path.join(self.hybrid_cache_dir, "hybrid_data_cache.pkl")
        embedding_cache_path = os.path.join(self.hybrid_cache_dir, "hybrid_embeddings.npy")
        
        # 三份文件全部存在就直接复用，不再次编码或训练。
        if os.path.exists(index_path) and os.path.exists(data_cache_path) and os.path.exists(embedding_cache_path):
            print(f"✅ Hybridindexexists: {self.hybrid_cache_dir}")
            print(f"   - index: {index_path}")
            print(f"   - data cache: {data_cache_path}")
            print(f"   - embedding cache: {embedding_cache_path}")
            return
        
        print(f"🔨 StartingBuildHybridindex...")
        print(f"   cacheDir: {self.hybrid_cache_dir}")
        
        # 读取每条边，得到结构化三元组、可编码的事实文本和对应时间。
        print("\n📊 Step1: from database Load triples...")
        triple_list, fact_list, time_list = self._load_triples_from_db()
        
        if not triple_list:
            print("❌ Cannot Load triples data")
            return
        
        print(f"✅ Loaddone {len(triple_list)} triples")
        
        # 保留向量位置与原始事实文本/时间的对应关系，供运行期回查。
        print("\n💾 Save data cache...")
        cache_data = {
            'triple_list': triple_list,
            'fact_list': fact_list,
            'time_list': time_list
        }
        with open(data_cache_path, 'wb') as f:
            pickle.dump(cache_data, f)
        print(f"✅ Data cache saved: {data_cache_path}")
        
        # 有 CUDA 且 use_gpu=True 时选择指定显卡，否则落到 CPU。
        print("\n🔄 Step2: LoadBGE-M3 model...")
        device = f'cuda:{gpu_id}' if torch.cuda.is_available() and use_gpu else 'cpu'
        print(f"   Device: {device}")
        
        try:
            model = BGEM3FlagModel(
                'BAAI/bge-m3',
                use_fp16=True,
                devices=[device]
            )
            print("✅ BGE-M3 model LoadSuccess")
        except Exception as e:
            print(f"❌ Model LoadFailed: {e}")
            return
        
        # 批量编码 fact_list，只保留 BGE-M3 的 dense_vecs 用于向量检索。
        print(f"\n🔄 Step3: Encode {len(fact_list)} facts...")
        try:
            embeddings = model.encode_corpus(
                fact_list,
                convert_to_numpy=True,
                batch_size=512,
                return_dense=True,
                return_sparse=False,
                return_colbert_vecs=False
            )
            
            embeddings = embeddings['dense_vecs']
            embeddings = embeddings.astype(np.float32)
            
            print(f"✅ Embedding vector shape: {embeddings.shape}")
            
            # float32 与 FAISS 输入类型一致，同时把向量保存为 .npy 缓存。
            np.save(embedding_cache_path, embeddings)
            print(f"✅ Embedding vector cache saved: {embedding_cache_path}")
            
        except Exception as e:
            print(f"❌ Encoding Failed: {e}")
            return
        
        # IndexIVFFlat 用内积检索；n_clusters 是聚类数，nprobe 是查询探测簇数。
        print("\n🔄 Step4: BuildFAISSindex...")
        try:
            embedding_size = embeddings.shape[1]
            n_clusters = 500
            nprobe = 60
            
            # IndexFlatIP 作为 IVF 的量化器；未训练前不能加入事实向量。
            quantizer = faiss.IndexFlatIP(embedding_size)
            index = faiss.IndexIVFFlat(quantizer, embedding_size, n_clusters, faiss.METRIC_INNER_PRODUCT)
            index.nprobe = nprobe
            
            # FAISS GPU 不可用时只回退索引构建到 CPU，不影响前面的模型编码。
            if use_gpu and torch.cuda.is_available():
                try:
                    print("   Trying to use GPU acceleration...")
                    res = faiss.StandardGpuResources()
                    index = faiss.index_cpu_to_gpu(res, gpu_id, index)
                    print("   ✅ GPU acceleration enabled")
                except Exception as e:
                    print(f"   ⚠️ GPU acceleration Failed, useCPU: {e}")
            
            # IVF 索引需要先用事实向量训练聚类中心。
            if not index.is_trained:
                print("   Training index...")
                index.train(embeddings)
                print("   ✅ index training completed")
            
            # 索引中的向量顺序与缓存的 fact_list 顺序保持一致。
            print("   Adding vectors to index...")
            index.add(embeddings)
            print(f"   ✅ Added {index.ntotal} vectors")
            
            # 保存前尝试转回 CPU 索引，以便使用 faiss.write_index 落盘。
            if use_gpu and torch.cuda.is_available():
                try:
                    index = faiss.index_gpu_to_cpu(index)
                except:
                    pass
            
            # 将索引序列化为 hybrid_index.bin。
            faiss.write_index(index, index_path)
            print(f"✅ index saved: {index_path}")
            
        except Exception as e:
            print(f"❌ index build Failed: {e}")
            import traceback
            traceback.print_exc()
            return
        
        print(f"\n{'='*80}")
        print(f"✅ Hybrid index build completed!")
        print(f"{'='*80}")
        print(f"cacheDir: {self.hybrid_cache_dir}")
        print(f"  - indexFile: hybrid_index.bin ({os.path.getsize(index_path) / 1024 / 1024:.1f} MB)")
        print(f"  - Data cache: hybrid_data_cache.pkl ({os.path.getsize(data_cache_path) / 1024 / 1024:.1f} MB)")
        print(f"  - Embedding cache: hybrid_embeddings.npy ({os.path.getsize(embedding_cache_path) / 1024 / 1024:.1f} MB)")
    
    def build_relation_embeddings(self):
        """为当前图谱中不同的关系名预计算本地 BGE-M3 嵌入。

        Args:
            无；使用 self.db_path 和 self.embedding_cache_dir。
        Returns:
            无返回值；已全部缓存时不重复计算。
        """
        print(f"\n{'='*80}")
        print(f"Building Relation Embedding Cache for {self.dataset_name}")
        print(f"{'='*80}")
        
        # RelationEmbeddingCache 位于 memotime/kg_agent，故先加入该模块路径。
        try:
            sys.path.insert(0, str(Path(__file__).parent.parent / 'memotime'))
            from kg_agent.embedding_cache import RelationEmbeddingCache
        except ImportError as e:
            print(f"❌ Cannot import RelationEmbeddingCache: {e}")
            print("   Please ensure TPKG/kg_agent/embedding_cache.py is available")
            return
        
        # 缓存目录随数据集分离，避免两个图谱的关系向量混用。
        try:
            print(f"📁 Embedding cache directory: {self.embedding_cache_dir}")
            cache = RelationEmbeddingCache(self.db_path, cache_dir=self.embedding_cache_dir)
            
            # 对比当前已缓存关系数与数据库中的关系总数。
            cache_info = cache.get_cache_info()
            print(f"📊 Current cache status:")
            print(f"   - Cached relations: {cache_info.get('cached_relations', 0)}")
            print(f"   - Total relations in DB: {cache_info.get('total_relations', 0)}")
            
            # 存在尚未缓存的关系时才执行预计算。
            if cache_info.get('cached_relations', 0) < cache_info.get('total_relations', 0):
                print(f"\n🔄 Precomputing relation embeddings...")
                cache.precompute_all_embeddings()
                print(f"✅ Relation embeddings precomputed successfully!")
            else:
                print(f"✅ All relation embeddings already cached")
            
            # 重新读取缓存统计，展示本次操作后的状态。
            final_info = cache.get_cache_info()
            print(f"\n📊 Final cache statistics:")
            print(f"   - Total relations: {final_info.get('total_relations', 0)}")
            print(f"   - Cached relations: {final_info.get('cached_relations', 0)}")
            print(f"   - Cache directory: {self.embedding_cache_dir}")
            
        except Exception as e:
            print(f"❌ Relation embedding building failed: {e}")
            import traceback
            traceback.print_exc()
    
    def _load_triples_from_db(self):
        """从 SQLite 边表生成事实索引所需的三种对齐序列。

        Args:
            无；读取 self.db_path。
        Returns:
            (triple_list, fact_list, time_list)：分别为三元组字符串、自然语言事实、
            时间对象列表；三者按同一条边的位置对齐。失败时返回三个空列表。
        """
        try:
            # 联结实体表，把边中的 head_id/tail_id 恢复为实体名，并按开始时间排序。
            conn = sqlite3.connect(self.db_path)
            
            # 这里只读取 t_start；结束时间和来源字段不进入事实索引文本。
            query = """
            SELECT 
                h.name as head,
                e.relation,
                t.name as tail,
                e.t_start
            FROM edges e
            JOIN entities h ON h.id = e.head_id
            JOIN entities t ON t.id = e.tail_id
            ORDER BY e.t_start_epoch
            """
            
            cursor = conn.cursor()
            cursor.execute(query)
            results = cursor.fetchall()
            conn.close()
            
            # 三个列表长度保持一致，位置 i 对应 SQLite 查询的第 i 条边。
            triple_list = []
            fact_list = []
            time_list = []
            
            for row in tqdm(results, desc="Load triples"):
                head, relation, tail, t_start = row
                
                # triple 保留关系标识；fact 用空格替换关系中的下划线，便于文本编码。
                triple = f"({head}, {relation}, {tail})"
                fact = f"{head} {relation.replace('_', ' ')} {tail}"
                if t_start:
                    fact += f" at {t_start}"
                
                triple_list.append(triple)
                fact_list.append(fact)
                
                # time_list 当前只保留年份：完整日期同样取年份的 1 月 1 日。
                # 解析不了的时间用 None 占位，仍保留该位置的事实文本。
                try:
                    if t_start:
                        # Support multiple time format
                        if '-' in t_start:
                            # YYYY-MM-DDformat
                            year = int(t_start.split('-')[0])
                        elif t_start.isdigit():
                            # Pure year
                            year = int(t_start)
                        else:
                            time_list.append(None)
                            continue
                        
                        time_obj = datetime(year, 1, 1).date()
                        time_list.append(time_obj)
                    else:
                        time_list.append(None)
                except:
                    time_list.append(None)
            
            return triple_list, fact_list, time_list
            
        except Exception as e:
            print(f"❌ from database Load triples Failed: {e}")
            import traceback
            traceback.print_exc()
            return [], [], []
    
    def run_all_steps(self, num_questions: int = None, build_hybrid: bool = False, build_embeddings: bool = False, gpu_id: int = 0):
        """按固定顺序执行建库、全量实体索引、候选生成及可选缓存。

        此便捷方法没有被下方 main() 调用；main() 单独处理 -n 实体子集逻辑。
        Args:
            num_questions: 候选生成处理的题数；None 表示全部。此方法建索引仍为全量。
            build_hybrid: 是否构建带时间事实的混合索引。
            build_embeddings: 是否预计算关系嵌入。
            gpu_id: 可选混合索引使用的 GPU 编号。
        Returns:
            无返回值；逐步打印处理状态。
        """
        print(f"\n{'='*80}")
        print(f"{self.dataset_name} Dataset Preprocessing")
        print(f"{'='*80}")
        
        # 建库和实体索引是候选生成的前置依赖。
        self.create_database()
        
        # Step2: Build entity index
        self.build_entity_index()
        
        # Step3: Generate candidate entities
        self.generate_candidates(num_questions=num_questions)
        
        # 以下两步只为运行期检索加速，可按命令行参数省略。
        if build_hybrid:
            self.build_hybrid_index(gpu_id=gpu_id)
        
        # Step5: Build relation embeddings (optional)
        if build_embeddings:
            self.build_relation_embeddings()
        
        print(f"\n{'='*80}")
        print(f"✅ {self.dataset_name} preprocessing completed!")
        print(f"{'='*80}")


def main():
    """解析命令行参数，并依次处理所选数据集。

    Args:
        无函数参数；选项由 argparse 从命令行读取。
    Returns:
        无返回值；各数据集的步骤会按独立 try 块执行。
    """
    import argparse
    
    # epilog 保留多行命令示例，便于通过 --help 查看常用组合。
    parser = argparse.ArgumentParser(
        description="Dataset preprocess script",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Process only first 10 questions for TimeQuestions
  python prepare_datasets.py --dataset TimeQuestions --num-questions 10
  
  # Quick test with 5 questions
  python prepare_datasets.py --test --dataset TimeQuestions
  
  # Process first 100 questions and build embeddings
  python prepare_datasets.py --dataset TimeQuestions --num-questions 100 --build-embeddings
  
  # Skip database and index, only generate candidates for 50 questions
  python prepare_datasets.py --skip-db --skip-index --num-questions 50
        """
    )
    # --dataset=all 依次处理两个数据集；不会把两者的数据库或索引混在一起。
    parser.add_argument("--dataset", type=str, choices=["TimeQuestions", "MultiTQ", "all"],
                       default="all", help="Dataset to process")
    # --test 强制只取前 5 题；--num-questions/-n 控制题目数量和新建索引的范围。
    parser.add_argument("--test", action="store_true", help="Test mode (only process first 5 questions)")
    parser.add_argument("--num-questions", "-n", type=int, default=None,
                       help="Number of questions to process (default: process all). Use -n 10, -n 50, -n 100 for quick tests. "
                            "Also limits entity index to the relevant subgraph of these questions")
    # --skip-db/--skip-index/--skip-candidates 跳过相应步骤，但不会自动检查产物是否齐全。
    parser.add_argument("--skip-db", action="store_true", help="Skip database creation")
    # --force-rebuild-db 明确删除并重建已有数据库；仅在未指定 --skip-db 时生效。
    parser.add_argument("--force-rebuild-db", action="store_true", help="Force rebuild database even if it exists")
    parser.add_argument("--skip-index", action="store_true", help="Skip index building")
    parser.add_argument("--skip-candidates", action="store_true", help="Skip candidate generation")
    # 后两项是运行期事实检索所需的可选缓存，不参与第三步候选实体打分。
    parser.add_argument("--build-hybrid", action="store_true", help="Build hybrid retrieval index")
    parser.add_argument("--build-embeddings", action="store_true", help="Build relation embedding cache")
    # --gpu-id 传给事实混合索引步骤；实体索引构建不接收这个参数。
    parser.add_argument("--gpu-id", type=int, default=0, help="GPU device ID (default: 0)")
    
    # parse_args 把命令行选项转成 args 的属性供下面的步骤分支读取。
    args = parser.parse_args()
    
    # --test 的赋值在这里覆盖用户另行传入的 -n 值。
    if args.test:
        args.num_questions = 5
    
    # all 代表分别处理 TimeQuestions 和 MultiTQ，而非合并成一个数据集。
    if args.dataset == "all":
        datasets_to_process = ["TimeQuestions", "MultiTQ"]
    else:
        datasets_to_process = [args.dataset]
    
    # 打印生效配置，便于确认是否跳过已有数据库/索引以及处理题数。
    print(f"\n{'='*80}")
    print("📋 CONFIGURATION SUMMARY")
    print(f"{'='*80}")
    print(f"Datasets to process: {', '.join(datasets_to_process)}")
    if args.num_questions:
        print(f"Questions per dataset: {args.num_questions} (limited)")
    else:
        print(f"Questions per dataset: ALL")
    print(f"Skip database: {args.skip_db}")
    print(f"Skip index: {args.skip_index}")
    print(f"Skip candidates: {args.skip_candidates}")
    print(f"Build hybrid index: {args.build_hybrid}")
    print(f"Build embeddings: {args.build_embeddings}")
    if args.build_hybrid:
        print(f"GPU ID: {args.gpu_id}")
    print(f"{'='*80}\n")
    
    # 每个数据集创建独立预处理器；一个数据集失败后仍继续尝试下一个。
    for dataset_name in datasets_to_process:
        try:
            preprocessor = DatasetPreprocessor(dataset_name)
            
            # Step 1：读取原始 KG 并建立 SQLite；已有有效库默认直接复用。
            if not args.skip_db:
                preprocessor.create_database(force_rebuild=args.force_rebuild_db)

            # Step 2：建立实体名向量索引。-n 模式尝试缩小待编码的实体范围。
            if not args.skip_index:
                # None 表示全量；若已有 HNSW 文件，build_entity_index 会直接复用旧索引。
                entity_ids = None
                if args.num_questions:
                    try:
                        # 必须与下一步 generate_candidates 使用相同的前 N 题。
                        with open(preprocessor.questions_file, 'r', encoding='utf-8') as f:
                            subset_questions = json.load(f)[:args.num_questions]
                        # 种子来自题面字面命中和可解析的答案标签；k=0 仍收集相邻边两端。
                        # 这种子集策略使用了测试题答案，只适合调试索引覆盖情况，
                        # 不能当作与全量、未知答案场景等价的候选生成实验。
                        entity_ids = preprocessor._relevant_entity_ids(subset_questions, k_hop=0)
                        if entity_ids:
                            print(f"📊 Entity subset for {len(subset_questions)} questions: {len(entity_ids)} entities")
                        else:
                            print("⚠️ empty entity subset, falling back to full index")
                    except Exception as e:
                        # 读题或计算子集失败时改为全量索引，避免得到不完整的索引。
                        print(f"⚠️ subset calculation failed ({e}), falling back to full index")
                        entity_ids = None
                preprocessor.build_entity_index(entity_ids=entity_ids)
            
            # Step 3：逐题抽关键词、语义召回实体并写入 questions_with_candidates_*.json。
            if not args.skip_candidates:
                preprocessor.generate_candidates(num_questions=args.num_questions)
            
            # Step 4/5：可选的事实混合索引及关系嵌入缓存。
            if args.build_hybrid:
                preprocessor.build_hybrid_index(gpu_id=args.gpu_id)
            
            if args.build_embeddings:
                preprocessor.build_relation_embeddings()
            
            print(f"\n✅ {dataset_name} processing completed!\n")
            
        except Exception as e:
            # 顶层异常按数据集隔离，打印堆栈后处理列表里的下一个数据集。
            print(f"\n❌ {dataset_name} processFailed: {e}\n")
            import traceback
            traceback.print_exc()
    
    print(f"\n{'='*80}")
    print("All Dataset Processing completed!")
    print(f"{'='*80}")


if __name__ == "__main__":
    # 仅直接运行本脚本时解析命令行；被其他模块导入时不会自动建库。
    main()

