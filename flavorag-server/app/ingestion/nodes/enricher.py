"""Enricher node — adds metadata to chunks (keywords, summary)."""

from __future__ import annotations

import re
from collections import Counter

from app.config.logging_config import get_logger
from app.ingestion.nodes.base import IngestionContext, NodeResult

_log = get_logger("flavorag.ingestion.enricher")

# Common Chinese/English stopwords
_STOPWORDS = set(
    "the is at which on and or not in to a of for with that it this was are be by "
    "的 是 在 和 了 有 不 人 这 中 大 为 上 个 国 我 以 要 他 时 来 用 们 生 到 "
    "作 地 于 出 就 分 对 成 会 可 主 发 年 动 同 工 也 能 下 过 子 说 产 种 "
    "面 而 方 后 多 定 行 学 法 所 民 得 经 十 三 之 进 着 等 部 度 家 电 力 "
    "里 如 水 化 高 自 二 理 起 小 物 现 实 加 量 都 两 体 制 机 当 使 点 从 "
    "业 本 去 把 性 应 开 它 还 因 由 其 些 然 前 外 天 政 四 日 那 社 义 事 "
    "平 形 相 全 表 间 样 与 关 各 重 新 线 内 数 正 心 反 你 明 看 原 又 么 "
    "利 比 或 但 质 气 第 向 道 命 此 变 条 只 没 结 解 问 意 建 月 公 无 系 "
    "很 情 者 最 立 代 想 已 通 并 提 直 题 程 展 五 果 料 象 员 革 位 入 常 "
    "文 总 次 品 式 活 设 及 管 特 件 长 求 老 头 基 资 边 流 路 级 少 图 山 "
    "统 接 知 较 将 组 见 计 别 她 手 角 期 根 论 运 农 指 几 九 区 强 放 决 "
    "西 被 干 做 必 战 先 回 则 任 取 据 处 队 南 给 色 光 门 即 保 治 北 造 "
    "百 规 热 领 七 海 口 东 导 器 压 志 世 金 增 争 济 阶 油 思 术 极 交 受 "
    "联 什 认 六 共 权 收 证 改 清 美 再 采 转 更 单 风 切 打 白 教 速 花 带 "
    "安 场 身 车 例 真 务 具 万 每 目 至 达 走 积 示 议 声 报 斗 完 类 八 离 "
    "华 名 确 才 科 张 信 马 节 话 米 整 空 元 况 今 集 温 传 土 许 步 群 广 "
    "石 记 需 段 研 界 拉 林 律 叫 且 究 观 越 织 装 影 算 低 持 音 众 书 布 "
    "复 容 儿 须 际 商 非 验 连 断 深 难 近 矿 千 周 委 素 技 备 半 办 青 省 "
    "列 习 响 约 支 般 史 感 劳 便 团 往 酸 历 市 克 何 除 消 构 府 称 太 准 "
    "精 值 号 率 族 维 划 选 标 写 存 候 毛 亲 快 效 斯 院 查 江 型 眼 王 按 "
    "格 养 易 置 派 层 片 始 却 专 状 育 厂 京 识 适 属 圆 包 火 住 调 满 县 "
    "局 照 参 红 细 引 听 该 铁 价 严".split()
)


class EnricherNode:
    """Enrich chunks with metadata: keywords, summary, title.

    Uses LLM to generate per-chunk metadata when enabled, otherwise
    falls back to heuristics (word frequency as keywords).

    Settings:
        use_llm (bool): Use LLM for enrichment (default False).
        enrich_fields (list[str]): Fields to enrich: keywords, summary, title.
    """

    NODE_TYPE = "enricher"

    async def __call__(self, ctx: IngestionContext) -> NodeResult:
        import time
        t0 = time.time()

        try:
            if not ctx.chunks:
                return NodeResult(
                    node_type=self.NODE_TYPE,
                    status="skipped",
                    message="No chunks to enrich",
                )

            use_llm = ctx.settings.get("use_llm", False)
            fields = ctx.settings.get("enrich_fields", ["keywords"])

            if use_llm:
                await self._enrich_with_llm(ctx, fields)
            else:
                self._enrich_heuristic(ctx, fields)

            duration_ms = int((time.time() - t0) * 1000)
            _log.info("enricher_done", doc_id=ctx.doc_id, chunk_count=len(ctx.chunks), took_ms=duration_ms)
            return NodeResult(
                node_type=self.NODE_TYPE,
                status="success",
                duration_ms=duration_ms,
                output={"chunk_count": len(ctx.chunks)},
            )
        except Exception as exc:
            duration_ms = int((time.time() - t0) * 1000)
            _log.error("enricher_failed", doc_id=ctx.doc_id, error=str(exc))
            return NodeResult(
                node_type=self.NODE_TYPE, status="error", error_message=str(exc), duration_ms=duration_ms,
            )

    async def _enrich_with_llm(self, ctx: IngestionContext, fields: list[str]):
        from app.llm.client import LLMClient
        from app.config.settings import settings

        client = LLMClient(
            base_url=settings.llm_base_url,
            api_key=settings.bailian_api_key or settings.siliconflow_api_key or "",
            model=settings.llm_model or "qwen-plus-latest",
        )
        for chunk in ctx.chunks:
            content = chunk.get("content", "")
            if not content.strip():
                continue
            prompt = "Extract 3-5 keywords from this text. Output as comma-separated list only.\n\nText:\n" + content[:1500]
            try:
                resp = await client.chat_completion(messages=[{"role": "user", "content": prompt}])
                meta = ctx.metadata.setdefault("enrichment", {}).setdefault(chunk.get("chunk_index", 0), {})
                meta["keywords"] = resp[:200]
            except Exception as exc:
                _log.warning("enricher_llm_failed", chunk_index=chunk.get("chunk_index"), error=str(exc))

    def _enrich_heuristic(self, ctx: IngestionContext, fields: list[str]):
        for chunk in ctx.chunks:
            content = chunk.get("content", "")
            if not content.strip():
                continue
            meta = ctx.metadata.setdefault("enrichment", {}).setdefault(chunk.get("chunk_index", 0), {})
            if "keywords" in fields:
                words = re.findall(r'\b[\u4e00-\u9fff\w]{2,}\b', content.lower())
                counter = Counter(w for w in words if w not in _STOPWORDS)
                keywords = [w for w, _ in counter.most_common(10)][:5]
                meta["keywords"] = ", ".join(keywords)
