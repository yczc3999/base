# Keyword 模块重构设计

**状态**：Draft  
**决策**：base 先行实现并验证，通过后再同步到下游项目。

---

## 1. 背景

当前 tag 模块来自早期 SEO 关键词采集实验，三方审计发现若干语义不清 / 耦合混乱点。本次只做**代码层面**的简化，AI prompt 文案业务化走后台 `settings.ai_review` 配置，不进代码。

关键前提：`tags` / `article_tags` / `search_keywords` 三表当前**零数据**，无迁移负担。

## 2. 真代码问题（共 4 项）

| # | 问题 | 影响 |
|---|---|---|
| 1 | 采集源仅 Google / Yandex / DuckDuckGo | 对中文业务（尤其国内产品）效果差 |
| 2 | `tags` 与 `search_keywords` 字段重复度 70%+，生命周期混在一起 | 代码维护困难，候选词和正式标签边界不清 |
| 3 | `source` 用 `SmallInt` 写死枚举 `0/1/2/3` | 加引擎需改代码 |
| 4 | `harvested` 字段名歧义，实指"是否已被当种子扩展过" | 命名误导，易写 bug |

## 3. 方案

### 3.1 表结构：单表 `keywords`（替代 `tags` + `search_keywords`）

**决策**：合并两表。候选池 + 已发布 canonical 合并用 `stage` 区分。

```sql
CREATE TABLE keywords (
    id              BIGSERIAL PRIMARY KEY,

    -- 关键字本体
    keyword         VARCHAR(200) NOT NULL,
    keyword_norm    VARCHAR(200) NOT NULL,            -- lower + trim + 压缩空白, 去重用
    slug            VARCHAR(200),                     -- 发布后才有 (stage=approved 时补)

    -- 生命周期
    stage           VARCHAR(16)  NOT NULL DEFAULT 'candidate',
                    -- candidate / approved / archived
    review_status   VARCHAR(16)  NOT NULL DEFAULT 'pending',
                    -- pending / ai_approved / ai_rejected / ai_uncertain / human_approved / human_rejected

    -- 采集溯源
    source_code     VARCHAR(32)  NOT NULL,            -- "manual" "google" "baidu" "sogou" ...
    seed_keyword    VARCHAR(200),
    expanded_as_seed_at TIMESTAMP,                    -- 被用作下一轮种子的时间 (命名比 harvested 清晰)
    fetched_at      TIMESTAMP    NOT NULL DEFAULT NOW(),

    -- GSC 回流指标 (原 search_keywords 的)
    metrics_json    JSONB        NOT NULL DEFAULT '{}',

    -- AI 审核产物
    ai_review_json  JSONB,                            -- {decision, reason, model, raw}

    -- Canonical 补充字段 (stage=approved 时才填)
    color           VARCHAR(20),
    description     VARCHAR(200),
    sort            INTEGER      NOT NULL DEFAULT 0,
    article_count   INTEGER      NOT NULL DEFAULT 0,

    created_at      TIMESTAMP    NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP    NOT NULL DEFAULT NOW()
);

-- 去重: keyword_norm 全局唯一 (同一词跨源只 1 条 canonical, 防多源带来重复 tag)
CREATE UNIQUE INDEX uq_keywords_norm ON keywords(keyword_norm);
CREATE UNIQUE INDEX uq_keywords_slug ON keywords(slug) WHERE slug IS NOT NULL;
CREATE INDEX idx_keywords_stage ON keywords(stage);
CREATE INDEX idx_keywords_review ON keywords(review_status) WHERE stage='candidate';
CREATE INDEX idx_keywords_seed_unexpanded ON keywords(expanded_as_seed_at) WHERE expanded_as_seed_at IS NULL;
CREATE INDEX idx_keywords_source_time ON keywords(source_code, fetched_at DESC);

-- 状态机一致性: 防 stage=approved + review_status=ai_rejected 这种漂移
ALTER TABLE keywords ADD CONSTRAINT chk_keywords_stage_review CHECK (
    (stage = 'candidate') OR
    (stage = 'approved' AND review_status IN ('human_approved', 'ai_approved')) OR
    (stage = 'archived' AND review_status IN ('human_rejected', 'ai_rejected'))
);
```

**文章关联** — 沿用现有 `article_tags` 改名 `article_keywords`（单表后 `tag_id` 改 `keyword_id`）：

```sql
CREATE TABLE article_keywords (
    article_id  BIGINT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    keyword_id  BIGINT NOT NULL REFERENCES keywords(id) ON DELETE CASCADE,
    is_primary  BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMP NOT NULL DEFAULT NOW(),
    PRIMARY KEY (article_id, keyword_id)
);
CREATE INDEX idx_ak_keyword_time ON article_keywords(keyword_id, created_at DESC);
CREATE INDEX idx_ak_primary ON article_keywords(keyword_id) WHERE is_primary = TRUE;
```

### 3.2 采集器扩展：加百度 + 搜狗

`services/keyword_harvester.py` 新增：

```python
async def suggest_baidu(seed: str) -> list[str]:
    """百度下拉 — https://suggestion.baidu.com/su?wd=XXX&cb=jsonp"""
    url = f"https://suggestion.baidu.com/su?wd={quote_plus(seed)}&action=opensearch"
    # 返回 [seed, [suggestions...]] JSONP/JSON 形式

async def suggest_sogou(seed: str) -> list[str]:
    """搜狗下拉 — https://www.sogou.com/suggnew/ajajjson?key=XXX"""
```

`harvest_recursive` 的 `engine_map` 新增：

```python
engine_map = {
    "google":     (suggest_google,     "google"),
    "duckduckgo": (suggest_duckduckgo, "ddg"),
    "yandex":     (suggest_yandex,     "yandex"),
    "baidu":      (suggest_baidu,      "baidu"),
    "sogou":      (suggest_sogou,      "sogou"),
}
```

**注意**：`engine_map` 第二位由 SmallInt 枚举 **改为 source_code 字符串**，与新表对齐。

### 3.3 source 改 VARCHAR

- Model：`source: Mapped[str] = mapped_column(String(32), nullable=False)`
- Harvester：直接传字符串 `"baidu"`，不再 0/1/2/3
- 前端 status/source 过滤下拉动态从 DB 的 `harvest_sources` 表（可选，先不做）或静态常量读

### 3.4 `harvested` → `expanded_as_seed_at`

- 字段类型从 `Boolean` 改 `TIMESTAMP`
- 读取语义从"布尔"升级为"扩展时间"，空值 = 未扩展
- 轮询采集 worker 用 `FOR UPDATE SKIP LOCKED` 原子抢种子：

  ```sql
  WITH c AS (
      SELECT id FROM keywords
      WHERE expanded_as_seed_at IS NULL AND stage = 'candidate'
      ORDER BY id
      FOR UPDATE SKIP LOCKED
      LIMIT 1
  )
  UPDATE keywords k
     SET expanded_as_seed_at = NOW(), updated_at = NOW()
    FROM c WHERE k.id = c.id
  RETURNING k.*;
  ```
  多 worker 并发天然不抢同一条，抢不到就 skip 下一条。

## 4. 代码文件变更

### 后端 (`serve/`)

| 路径 | 变更 |
|---|---|
| `app/models/tag.py` | **删除** |
| `app/models/article_tag.py` | **删除** |
| `app/models/search_keyword.py` | **删除** |
| `app/models/keyword.py` | 新增，见 §3.1 |
| `app/models/article_keyword.py` | 新增，见 §3.1 |
| `app/models/__init__.py` | 导出调整 |
| `app/logics/tag.py` / `article_tag.py` / `search_keyword.py` | **删除** |
| `app/logics/keyword.py` | 新增（合并三者 + stage 区分） |
| `app/logics/article_keyword.py` | 新增（极简，基本等同 article_tag） |
| `app/controllers/admin/tag.py` / `search_keyword.py` | **删除** |
| `app/controllers/admin/keyword.py` | 新增 |
| `app/services/keyword_harvester.py` | 加 `suggest_baidu` / `suggest_sogou`；`engine_map` 改字符串 code |
| `app/services/ai_content.py` | 调用 `keyword_logic` 而非 `tag_logic`；仍走 `settings.ai_review` |
| `app/main.py` | router 注册更新 |
| `app/logics/article.py` | 关联查询 `article_keywords` 替代 `article_tags` |

### 前端 (`admin/`)

| 路径 | 变更 |
|---|---|
| `src/views/content/tag/index.vue` | **删除**（或保留做向后兼容跳转） |
| `src/views/content/keyword/index.vue` | 重写成统一的候选池 + canonical 管理 |
| `src/api/modules/tag.ts` | **删除** |
| `src/api/modules/keyword.ts` | 新增 |

### DB Migration

由于**零数据**，不做双写 / 回滚映射，直接 DROP + CREATE：

```sql
-- migration: XXX_keyword_unify.sql
BEGIN;

-- 1) DROP 前强断言: 三表必须全空, 否则 EXCEPTION (保护生产)
DO $$ BEGIN
  IF (SELECT COUNT(*) FROM tags) + (SELECT COUNT(*) FROM search_keywords)
   + (SELECT COUNT(*) FROM article_tags) <> 0
  THEN RAISE EXCEPTION 'non-empty legacy tag tables; migration aborted';
  END IF;
END $$;

DROP TABLE IF EXISTS article_tags;
DROP TABLE IF EXISTS search_keywords;
DROP TABLE IF EXISTS tags;

CREATE TABLE keywords ( /* §3.1 */ );
CREATE INDEX ... ;
CREATE UNIQUE INDEX ... ;
ALTER TABLE keywords ADD CONSTRAINT chk_keywords_stage_review ...;

CREATE TABLE article_keywords ( /* §3.1 */ );
CREATE INDEX ... ;

-- 2) 菜单 seed 幂等 (UPSERT 模式, 不 UPDATE + DELETE 避免误删)
-- 2a) 如旧的 content-tag 存在, 先改 slug 为 content-keyword
UPDATE menus
   SET label='关键词管理', path='/content/keyword',
       template_path='content/keyword/index', slug='content-keyword'
 WHERE slug='content-tag'
   AND NOT EXISTS (SELECT 1 FROM menus WHERE slug='content-keyword');

-- 2b) 都不存在则插入
INSERT INTO menus (parent_id, label, slug, path, template_path, icon, sort, type, is_visible, is_cache)
SELECT m.id, '关键词管理', 'content-keyword', '/content/keyword',
       'content/keyword/index', 'Search', 17, 1, true, true
  FROM menus m WHERE m.slug='content'
 AND NOT EXISTS (SELECT 1 FROM menus WHERE slug='content-keyword');

-- 2c) 重复的 content-tag 清理 (只有 2a 都没走才可能残留)
DELETE FROM menus WHERE slug='content-tag';

COMMIT;
```

## 5. 实施顺序（base 先行）

1. **base 实施**：按 §4 清单改代码 + 跑 migration + e2e 自测（采集 / 审核 / 发布闭环）
2. **验证**：新增种子 → 百度/搜狗/Google 采集 → AI 审核 → 人工上线 → 生成文章
3. **chinaExportCarCheck 同步**：完全复制 base 的代码差，同跑 migration（本项目业务词不同，AI prompt 后台自配）

## 6. 非目标（本次不做）

- ❌ SSE checkpoint / 可恢复任务流
- ❌ AI domain_profile 多租户化
- ❌ 语义归并（embedding / CJK 分词）
- ❌ `harvest_sources` 注册表（source VARCHAR 已足够，未来扩展再说）
- ❌ 采集 adapter 插件制（直接函数表，没到需要抽象层次）

## 7. 风险与回滚

| 风险 | 应对 |
|---|---|
| 数据已存在 | 跑 migration 前 `SELECT count(*) FROM tags` 再次确认 0 行；非 0 则转软迁移分支 |
| 外部代码引用 `Tag` / `ArticleTag` | 代码层 grep 清零；保留一版路由 301 到新路径做前端兼容 |
| 百度 / 搜狗 API 反爬 | User-Agent 轮换 + 请求间隔抖动（已有），失败直接跳过不影响整体采集 |

## 8. Size 估算

- 后端改造 ~600 行（含 model / logic / controller / service / migration）
- 前端改造 ~300 行（统一候选池 + canonical 页面）
- 预计总工时 **1.5-2 天**（base 后端 ≈ 8h，前端重写 ≈ 4h，采集/AI 闭环联调 ≈ 2h，chinaExportCarCheck 同步 ≈ 2h）
- 关键前置：实施前 `grep -rn "Tag\|ArticleTag\|SearchKeyword" serve/app/` 把所有旧链路引用列清单，漏改会炸

---

**下一步**：等用户确认 → 在 base 开干。
