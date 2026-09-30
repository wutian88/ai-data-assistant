import os
from pathlib import Path

from langchain_core.documents import Document
from langchain_milvus import Milvus

from app.core.llm import embeddings
from app.core.paths import MILVUS_DB, MILVUS_INIT_MARKER


# ============================================================
# 1. Milvus Lite 数据库路径
# ============================================================

# ============================================================
# 2. 初始化成功标记
#
# 不能只判断 milvus_day14.db 是否存在。
#
# 原因：
# Milvus 连接空数据库时，也可能先创建 DB 文件，
# 但里面还没有真正写入知识文档。
#
# 所以只有初始化真正成功以后，
# 才创建这个 marker 文件。
# ============================================================

INIT_MARKER = MILVUS_INIT_MARKER


# ============================================================
# 3. 演示知识库
# ============================================================

docs = [
    Document(
        page_content="商品支持签收后7天内申请退款。",
        metadata={
            "source": "售后规则",
            "category": "退款",
        },
    ),

    Document(
        page_content="退款申请审核通过后，款项将在3个工作日内原路退回。",
        metadata={
            "source": "售后规则",
            "category": "退款",
        },
    ),

    Document(
        page_content="商品出现质量问题时，可以申请退货或者换货。",
        metadata={
            "source": "售后规则",
            "category": "退换货",
        },
    ),

    Document(
        page_content="非质量问题商品需要保持商品完整，不影响二次销售。",
        metadata={
            "source": "售后规则",
            "category": "退换货",
        },
    ),

    Document(
        page_content="会员用户每月可以领取一张满100减10元优惠券。",
        metadata={
            "source": "会员规则",
            "category": "会员",
        },
    ),
]


# ============================================================
# 4. 初始化知识库
# ============================================================

def init_knowledge() -> None:

    # DB + marker 都存在，
    # 才认为知识库已经真正初始化完成。
    if (
        MILVUS_DB.exists()
        and INIT_MARKER.exists()
    ):
        print(
            f"[init-rag] knowledge database already initialized: "
            f"{MILVUS_DB}"
        )
        return

    MILVUS_DB.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    print(
        f"[init-rag] initializing knowledge database: "
        f"{MILVUS_DB}"
    )

    # 如果之前初始化失败过，
    # 先确保不存在错误的成功标记。
    if INIT_MARKER.exists():
        INIT_MARKER.unlink()

    Milvus.from_documents(
        documents=docs,
        embedding=embeddings,
        collection_name="knowledge",
        connection_args={
            "uri": str(MILVUS_DB),
        },
        index_params={
            "index_type": "FLAT",
            "metric_type": "COSINE",
            "params": {},
        },

        # 演示知识库初始化时重新创建 collection，
        # 避免重复插入文档。
        drop_old=True,
    )

    # 只有上面全部成功后才创建 marker。
    INIT_MARKER.write_text(
        "initialized",
        encoding="utf-8",
    )

    print(
        "[init-rag] knowledge database initialized successfully"
    )
    print(
        f"[init-rag] documents: {len(docs)}"
    )
    print(
        f"[init-rag] database: {MILVUS_DB}"
    )


# ============================================================
# 5. Script Entry
# ============================================================

if __name__ == "__main__":
    init_knowledge()