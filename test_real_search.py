"""
真实语义搜索测试 - 使用 OpenRouter 真实 embedding 进行 RAG 检索

演示如何使用自然语言问题检索代码库中最相关的代码片段。
需要在 .env 中配置有效的 OPENROUTER_API_KEY 并设置 USE_MOCK_EMBEDDING=false。

注意: 需要先运行 run_pipeline.py 将代码库摄入 Qdrant。
"""

import logging
from qdrant_client import QdrantClient
from ingestion.config import QDRANT_HOST, QDRANT_DEFAULT_PORT, DEFAULT_COLLECTION_NAME
from ingestion.embedding import get_embeddings

# 关闭底层 http 库的繁琐日志，只看我们的核心结果
logging.getLogger("httpx").setLevel(logging.WARNING)

def semantic_search_test():
    # 1. 连接 Qdrant
    client = QdrantClient(host=QDRANT_HOST, port=QDRANT_DEFAULT_PORT)
    
    # 2. 输入你想问的自然语言问题 (大白话)
    # 因为你刚刚入库的是 game_hook 的代码，我们故意问一个偏业务的自然语言问题
    query = "音频数据是怎么被捕获并传递出去的？"
    
    print("=" * 60)
    print(f"你的问题: {query}")
    print("=" * 60)
    print("正在调用 OpenRouter 将你的问题转化为向量...\n")

    # 3. 将问题也转成真实向量 (注意这里传入的是单元素的列表)
    query_vectors = get_embeddings([query])
    query_vector = query_vectors[0]

    # 4. 去 Qdrant 里进行纯语义的余弦相似度检索
    # 新版 qdrant-client 使用 query_points() 而非 search()
    search_result = client.query_points(
        collection_name=DEFAULT_COLLECTION_NAME,
        query=query_vector,  # 注意: 新 API 使用 query 而非 query_vector
        limit=3,  # 只召回最相关的前 3 块代码
        with_payload=True,  # 明确要求返回 payload
        with_vectors=False  # 不需要返回向量本身
    )

    # 5. 打印结果
    # 新版 API 返回 QueryResponse 对象，需要访问 .points 属性
    print("RAG 召回的 Top 3 最相关 C++ 代码块:\n")
    for i, hit in enumerate(search_result.points, 1):
        payload = hit.payload
        # 相似度得分 (Score) 越接近 1.0 越相关
        print(f"--- [Top {i} | 相似度得分: {hit.score:.4f}] ---")
        print(f"实体: {payload.get('entity_type')} -> {payload.get('entity_name')}")
        print(f"文件: {payload.get('file_path')}")
        
        doc = payload.get('docstring')
        if doc:
            print(f"注释:\n{doc}\n")
            
        print(f"源码片段:\n{payload.get('code_text')[:300]} ...[截断]...\n")

if __name__ == "__main__":
    semantic_search_test()
