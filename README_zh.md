<p align="center">
  <img src="MemoTime_logo.png" alt="MemoTime_logo" width="180"/>
</p>



# MemoTime：记忆增强的时序知识图谱助力大模型推理 [WWW 2026]


<p align="center">
  <!-- 项目主页 -->
  
<a href="https://stevetantan.github.io/MemoTime/" target="_blank">
  <img src="MemoTime_logo.png" alt="Memotime Logo" height="28" style="vertical-align:middle; margin-right:4px;">
  <img alt="Project Page"
       src="https://img.shields.io/badge/Page-Homepage-00BFFF?style=for-the-badge&logoColor=white">
</a>


  <!-- arXiv -->
  <a href="https://arxiv.org/abs/2510.13614" target="_blank">
    <img alt="arXiv"
         src="https://img.shields.io/badge/arXiv-2510.13614-FF4500?style=for-the-badge&logo=arxiv&logoColor=white">
  </a>




  <!-- Stars -->
  <a href="https://github.com/SteveTANTAN/MemoTime/stargazers" target="_blank">
    <img alt="GitHub Stars"
         src="https://img.shields.io/github/stars/SteveTANTAN/MemoTime?style=for-the-badge&label=stars&color=FFD700&logo=github&logoColor=black">
  </a>


</p>

## 新闻！
我们的论文已被 WWW 2026 接收！

## 如何引用
如果你对本工作感兴趣或受到启发，欢迎引用：

```sh
@inproceedings{tan2026memotime,
  title={Memotime: Memory-augmented temporal knowledge graph enhanced large language model reasoning},
  author={Tan, Xingyu and Wang, Xiaoyang and Liu, Qing and Xu, Xiwei and Yuan, Xin and Zhu, Liming and Zhang, Wenjie},
  booktitle = {Proceedings of the ACM Web Conference 2026},
  pages = {4220–4231},
  year={2026}
}
```

---

## 📄 摘要

大语言模型展现出强大的推理能力，但当问题涉及多个实体、复合算子和不断演变的事件序列时，往往难以保持**时序一致性**。

<p align="center">
  <img src="MemoTime.jpg" alt="MemoTime"/>
</p>


**MemoTime** 针对四个关键挑战：
(1) 在多跳推理中保持时序忠实性；
(2) 让多个实体沿共享时间线同步；
(3) 使检索适配多样的时序算子；
(4) 复用已有推理经验以提升效率与稳定性。
MemoTime 将复杂的时序问题分解为分层的**时间之树（Tree of Time）**，实现算子感知的推理、动态证据检索，以及可持续自我改进的经验记忆。


---

## 📦 项目结构

```
MemoTime/
├── memotime/                       # 主源代码
│   ├── run.py                      # 命令行入口
│   ├── main.py                     # 实验运行器
│   ├── config.py                   # 配置管理
│   ├── data_process.py             # 数据集处理
│   ├── experiment_database.py      # 实验追踪
│   └── kg_agent/                   # 核心推理引擎
│       ├── agent.py                # 主推理智能体
│       ├── stepwise.py             # 递归分解逻辑
│       ├── decompose.py            # 问题分解
│       ├── hybrid_retrieval.py     # 混合检索策略
│       ├── unified_knowledge_store.py  # 经验记忆
│       ├── answer_verifier.py      # 答案验证
│       ├── prompts.py              # 通用提示词
│       ├── fixed_prompts.py        # 工具箱初始化提示词
│       ├── llm.py                  # 大模型接口
│       └── ...                     # 其他组件
├── Data/                           # 数据集目录
│   ├── prepare_datasets.py         # 数据集准备脚本
│   ├── TimeQuestions/              # TimeQuestions 数据集
│   └── MultiTQ/                    # MultiTQ 数据集

```

---

## 🚀 快速开始

### 1. 安装

```bash
git clone <URL>
cd WWW-MemoTime-Submit

# 安装依赖
bash setup_dependencies.sh

# 验证安装
python verify_installation.py

```

### 2. 配置 API Key

编辑 `memotime/kg_agent/llm.py` 中的以下行：

```python
DEFAULT_OPENAI_API_KEY = "your-openai-api-key"
```

### 3. 准备数据集
该流程包括全图构建、图索引、混合索引以及主题实体识别。你可以通过 `-n` 参数选择用于实体识别的问题数量。

```bash
cd Data
# 准备全部数据集
python prepare_datasets.py --dataset all --build-hybrid --build-embeddings

# 只准备 MultiTQ
python prepare_datasets.py --dataset MultiTQ --build-hybrid --build-embeddings

# 只处理 20 个 TimeQuestions 问题
python prepare_datasets.py --dataset TimeQuestions -n 20

# 如果已有数据库和索引，仅为 20 个问题生成候选实体
python prepare_datasets.py --dataset TimeQuestions -n 20 --skip-db --skip-index

```

---

## 💻 使用方法

### 基本命令

```bash
cd ../memotime
# 运行实验
python run.py --questions 5 --dataset MultiTQ

# 使用详细配置运行
python run.py --retries 2 --depth 3 --hybrid --unified-knowledge --dataset MultiTQ --questions 50

# 按问题类型运行
python run.py --dataset MultiTQ --type equal --questions 20
```

### 查看帮助与结果

```bash
# 显示所有可用选项
python run.py --help
```

**参数分类：**

* **实验配置：** `--dataset`、`--name`、`--desc`、`--tags`
* **功能开关：** `--hybrid`、`--unified-knowledge`
* **存储模式：** `--storage-mode`、`--enable-shared-fallback`、`--config-name`
* **实验范围：** `--questions`、`--skip`、`--type`、`--entities`
* **大模型配置：** `--model`
* **其他：** `--result`、`--no-save`、`--verbose`

```bash
# 查看实验结果
python run.py --result --name my_experiment
python run.py --result --preset accuracy_mode
```

**结果视图包含：**

* 配置摘要与运行历史
* 总体及各问题类型的准确率
* 答案类型与时序粒度统计

---

## 📄 许可证

基于 **MIT 许可证**发布，详见 [LICENSE](LICENSE)。
