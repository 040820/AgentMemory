# =============================
# file: kg_agent/local_embed.py
# =============================
# 本地 BGE-M3 embedding（替代 OpenAI embedding API，MiMo 无 embeddings 端点）。
# 模型与 hybrid 检索共用 BAAI/bge-m3，首次调用时惰性加载。

import numpy as np

_MODEL = None


def get_model():
    """惰性加载 BGE-M3（有 CUDA 用 GPU，否则 CPU）"""
    global _MODEL
    if _MODEL is None:
        import torch
        from FlagEmbedding import BGEM3FlagModel
        use_cuda = torch.cuda.is_available()
        device = "cuda:0" if use_cuda else "cpu"
        _MODEL = BGEM3FlagModel(
            "BAAI/bge-m3",
            use_fp16=use_cuda,
            devices=[device],
        )
    return _MODEL


def embed_texts(texts, batch_size: int = 256) -> np.ndarray:
    """把字符串列表编码为 L2 归一化的 embedding 矩阵 (N, 1024)"""
    texts = [str(t) for t in texts]
    model = get_model()
    out = model.encode(
        texts,
        batch_size=batch_size,
        max_length=256,
        convert_to_numpy=True,
        return_dense=True,
        return_sparse=False,
        return_colbert_vecs=False,
    )
    arr = out["dense_vecs"].astype(np.float32)
    arr /= (np.linalg.norm(arr, axis=1, keepdims=True) + 1e-12)
    return arr
