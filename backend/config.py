"""
FOTA 智能诊断平台 — 统一配置模块

本模块使用 pydantic-settings 管理应用配置，支持从 .env 文件和环境变量读取配置项。
提供两种部署模式的自动切换：
- 场景 A：国内部署，通过 LiteLLM 网关中转访问 LLM 服务
- 场景 B：海外部署，直连 LLM 供应商 API

主要功能：
1. 数据库连接配置（PostgreSQL、Redis）
2. LLM 服务配置（支持多供应商）
3. 场景化 Agent 映射配置
4. 根据部署模式自动选择 API 端点和密钥

作者：FOTA 诊断平台团队
创建时间：2025
最后更新：2025
"""

from enum import Enum
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class DeploymentMode(str, Enum):
    """
    部署模式枚举

    定义系统的两种部署场景，影响 LLM 服务的访问方式：
    - SCENARIO_A: 国内部署模式，通过 LiteLLM 网关中转
    - SCENARIO_B: 海外部署模式，直连供应商 API
    """
    SCENARIO_A = "A"  # 平台在国内，走 LiteLLM 中转
    SCENARIO_B = "B"  # 平台在海外，直连供应商


class Settings(BaseSettings):
    """
    应用配置类

    使用 pydantic-settings 自动从 .env 文件和环境变量加载配置。
    所有配置项都有合理的默认值，可通过环境变量覆盖。

    配置分类：
    1. 基础配置：项目名称、部署模式
    2. 数据库配置：PostgreSQL、Redis 连接参数
    3. LLM 配置：多供应商 API 密钥和端点
    4. 编排器配置：流式输出等行为控制

    派生属性：
    - DATABASE_URL: 自动生成的数据库连接字符串
    - LLM_BASE_URL: 根据部署模式自动选择的 LLM 端点
    - LLM_API_KEY: 根据部署模式自动选择的 API 密钥
    """
    # ── 基础配置 ──
    PROJECT_NAME: str = "FOTA 智能诊断平台"
    DEPLOYMENT_MODE: DeploymentMode = DeploymentMode.SCENARIO_A

    # ── 数据库配置（待接入） ──
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "fota_password"
    POSTGRES_DB: str = "fota_db"
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432

    # ── Redis 配置（待接入） ──
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: Optional[str] = None

    # ── LLM 配置 ──
    LITELLM_BASE_URL: Optional[str] = "http://127.0.0.1:4000/v1"
    LITELLM_API_KEY: Optional[str] = "sk-fota-virtual-key"

    # ── 存储配置 ──
    # 日志文件存储根目录
    STORAGE_ROOT: str = "/opt/fota-backend/data"

    ANTHROPIC_API_KEY: Optional[str] = None
    OPENAI_API_KEY: Optional[str] = None

    # 供应商专用 Base URL (场景 B 直连模式使用)
    ANTHROPIC_API_BASE: str = "https://api.anthropic.com"
    OPENAI_API_BASE: str = "https://api.openai.com/v1"

    # ── 编排器 ──
    ORCHESTRATOR_STREAM: bool = False

    # ── 派生属性 ──

    @property
    def DATABASE_URL(self) -> str:
        """
        生成 PostgreSQL 同步连接字符串

        使用 psycopg2 驱动，供 SQLAlchemy ORM 使用。

        Returns:
            str: 格式为 postgresql://user:password@host:port/database 的连接串
        """
        return (
            f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def DATABASE_URL_ASYNC(self) -> str:
        """
        生成 PostgreSQL 异步连接字符串

        使用 asyncpg 驱动，供异步操作使用。

        Returns:
            str: 格式为 postgresql+asyncpg://user:password@host:port/database 的连接串
        """
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def LLM_BASE_URL(self) -> Optional[str]:
        """
        根据部署模式自动选择 LLM 服务的 Base URL

        - 场景 A（国内）：返回 LiteLLM 网关地址
        - 场景 B（海外）：返回 None，使用 OpenAI SDK 默认端点

        Returns:
            Optional[str]: LLM API 的基础 URL，场景 B 返回 None
        """
        if self.DEPLOYMENT_MODE == DeploymentMode.SCENARIO_A:
            return self.LITELLM_BASE_URL
        # 场景 B 下，如果用户明确设置了 OPENAI_API_BASE（非官方默认），则返回它作为全局透传地址
        # 否则返回 None 让 SDK 自行处理多供应商默认端点
        if self.OPENAI_API_BASE != "https://api.openai.com/v1":
            return self.OPENAI_API_BASE
        return None

    @property
    def LLM_API_KEY(self) -> Optional[str]:
        """
        根据部署模式自动选择 LLM 服务的 API Key

        - 场景 A（国内）：返回 LiteLLM 网关的统一密钥
        - 场景 B（海外）：返回第一个可用的供应商密钥（优先 Anthropic）

        Returns:
            Optional[str]: LLM API 密钥
        """
        if self.DEPLOYMENT_MODE == DeploymentMode.SCENARIO_A:
            return self.LITELLM_API_KEY
        # 场景 B 需区分供应商，此处取第一个可用 Key
        return self.ANTHROPIC_API_KEY or self.OPENAI_API_KEY

    # ── Workspace（诊断工作区沙盒）──
    WORKSPACE_ENABLED: bool = True  # 是否启用 Markdown 工作区
    WORKSPACE_MAX_SIZE_MB: int = 1024  # 工作区总容量上限（MB）

    # ── Agent LLM 开关 ──
    # True  = Agent 调用 LLM 生成诊断（无 Key 或调用失败时自动降级到 mock）
    # False = 强制全局 mock，适用于离线测试 / CI
    AGENTS_USE_LLM: bool = True

    # ── 内部服务地址（Agent 内部调用 log_pipeline API 使用）──
    BACKEND_BASE_URL: str = "http://localhost:8000"

    # ── Embedding 向量检索开关 ──
    # True  = 使用 OpenAI Embedding API 做真实向量检索（需 OPENAI_API_KEY）
    # False = 使用 TF-IDF baseline（无需 API Key，默认）
    AGENTS_USE_EMBEDDINGS: bool = False

    # 预计算 embedding 索引的存放目录（相对于 backend/data/）
    VECTOR_INDEX_DIR: str = "indexes/vector"

    # ── 文档切块配置（doc_chunker.py / doc_retrieval.py 使用）──
    # 滑动窗口每块的目标字符数
    DOC_CHUNK_SIZE: int = 500
    # 相邻 chunk 之间的重叠字符数（提高跨块关键词召回率）
    DOC_CHUNK_OVERLAP: int = 80
    # 短文档直接入库的字符数阈值；超过此值时启用滑动窗口切块
    DOC_CHUNK_INLINE_THRESHOLD: int = 600

    # ── CORS 配置 ──
    # 逗号分隔的允许来源列表，生产环境应设置为具体域名
    # 示例: ALLOWED_ORIGINS=https://fota.example.com,https://admin.example.com
    ALLOWED_ORIGINS: str = "http://localhost:3000,http://127.0.0.1:3000"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()


# ── 场景化 Agent 映射（Velab 编排层使用）──
SCENARIO_AGENT_MAP: dict[str, list[str]] = {
    # 基础 FOTA 诊断：日志分析 + 技术文档检索（无 Jira）
    "fota-diagnostic": ["log_analytics", "doc_retrieval"],
    # FOTA + Jira：增加历史工单匹配，适合已有历史数据的问题
    "fota-jira": ["log_analytics", "jira_knowledge", "doc_retrieval"],
    # 车队数据分析：聚焦日志统计分析（无文档/Jira）
    "fleet-analytics": ["log_analytics"],
    # CES Demo：全量 Agent，叠加 RCA 综合分析（展会演示用全链路）
    "ces-demo": ["log_analytics", "jira_knowledge", "doc_retrieval", "rca_synthesizer"],
    # 数据采集演示：仅日志解析管线
    "data-acquisitions": ["log_analytics"],
}
