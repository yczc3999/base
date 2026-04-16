# SEO 发布策略 — 阈值基准

> **本文是 SEO 模块的"宪法"**。所有数值必须有出处；想调数值，先更新本文再改代码。
> 每季度复核一次，实际数据偏离 20%+ 时更新基准。

## 0. 模块性质声明（诚实披露）

本模块是一个**规模化 SEO 内容工具**，用于自动采集 + AI 润色 + 定时发布。
它**不是**"Google 白名单批准的做法"，而是在**尊重 Google 质量要求前提下**的半自动化运营系统。

Google 2026 的 [Scaled Content Abuse Policy](https://developers.google.com/search/docs/essentials/spam-policies)
重点打击"规模化低质内容"。本模块通过以下手段让产出的内容**够不上"低质"的判定**：

- 强制 AI 润色（非机械翻译/改写）
- 字数闸门（800+）
- 指纹去重（simhash hamming ≥ 10）
- 同标签节流（7 天内 ≤ 2 篇）
- 阶段化节奏（cold 阶段人工 approve，stable 阶段 10% 抽查）
- 独创性提示（AI prompt 要求每篇含 1 具体案例 + 1 反直觉观点 + 1 真实外链）

**法律/道德界限**：不要冒用原作者名，采集素材只作为 AI 润色的**参考**而非拷贝源；
每篇重要信息点必须添加真实来源外链。

---

## 1. 阶段分类算法

### 1.1 输入

| 指标 | 来源 | 缺失时 |
|---|---|---|
| `age_days` | `NOW() - settings.site.launched_at` | 缺省视为 0（冷启动） |
| `published_cnt` | `COUNT(articles WHERE status=1 AND deleted_at IS NULL AND published_at<=NOW())` | 缺省为 0 |
| `indexed_cnt` | GSC API / Bing API（Phase 2），MVP 阶段 NULL | 缺省按 age+published 分类 |

### 1.2 健康索引率曲线

```python
def expected_index_rate(age_days: int) -> float:
    """期望的 indexed/published 比例 —— 一个健康站点此站龄下应达到的索引率。
    
    曲线基于 IndexCheckr 16M 页研究拟合。⚠️ 该研究样本偏英文宽类目，
    中文小众/回国加速类 niche 需上线 3 个月后校准。
    """
    if age_days < 7:    return 0.00   # 尚未完成首爬
    if age_days < 14:   return 0.14   # 研究：1 周 14%
    if age_days < 30:   return 0.50   # 研究：30 天 ~65%，取中位
    if age_days < 90:   return 0.65   # 长尾爬取期
    if age_days < 180:  return 0.75   # 成熟早期
    else:               return 0.85   # 稳态
```

### 1.3 健康度得分

```python
def index_health_score(indexed: int | None, published: int, age_days: int) -> float | None:
    """返回 actual / expected 比值；GSC 未接入时返 None。"""
    if indexed is None:
        return None
    actual = indexed / max(published, 1)
    expected = expected_index_rate(age_days)
    return round(actual / max(expected, 0.01), 2)
```

| score 区间 | 状态 | 含义 |
|---|---|---|
| ≥ 1.0 | 🟢 优秀 | 实际索引率达到/超过预期，可 daily_cap+1 浮动奖励 |
| 0.7 ~ 1.0 | 🟢 健康 | 按阶段默认配置 |
| 0.4 ~ 0.7 | 🟡 观察 | 按阶段默认，不加速 |
| < 0.4 | 🔴 异常 | 强制降档（可能被 sandbox 压制或部分 deindex） |

### 1.4 分类决策树

```python
def classify_phase(age_days, published_cnt, indexed_cnt) -> tuple[str, str]:
    """返回 (phase, reason)。缺 GSC 时优雅降级。"""
    # 冷启动：绝对新站 OR 内容极少
    if age_days < 30 or published_cnt < 3:
        return "cold", f"建站 {age_days} 天, 已发 {published_cnt} 篇 (冷启动阈 30 天/3 篇)"

    health = index_health_score(indexed_cnt, published_cnt, age_days)

    # 有 GSC 数据 → 用健康度精判
    if health is not None:
        if health < 0.4:
            return "new", f"索引健康度 {health} 异常 (<0.4), 强制降档"
        if age_days < 90:
            return "new", f"sandbox 期 (<90 天), 健康度 {health}"
        if age_days < 180 or health < 0.7:
            return "growing", f"成长期 / 健康度 {health} 未达稳态阈 0.7"
        if published_cnt >= 30 and health >= 0.7:
            return "stable", f"6 月+, 已发 ≥30 篇, 健康度 {health} ≥ 0.7"
        return "growing", "兜底"

    # 无 GSC → 退化为 age + published_cnt
    if age_days < 90:
        return "new", f"sandbox 期 (age={age_days}d, 无 GSC)"
    if age_days < 180:
        return "growing", f"成长期 (age={age_days}d, 无 GSC)"
    if published_cnt >= 30:
        return "stable", f"6 月+, 已发 {published_cnt} 篇 (无 GSC)"
    return "growing", f"age={age_days}d, published={published_cnt} (未达稳态发布量)"
```

---

## 2. 每阶段配额表

| 阶段 | 触发条件 | daily_cap | weekly_cap | 需人工 approve | 抽查 | IndexNow |
|---|---|---|---|---|---|---|
| **cold** | age<30 OR pub<3 | 1 | 3 | ✅ 必需 | — | ❌ |
| **new** | age<90 OR health<0.4 | 1 | 5 | ❌ | — | ✅ |
| **growing** | age<180 OR health<0.7 | 2 | 10 | ❌ | — | ✅ |
| **stable** | age≥180 AND pub≥30 AND health≥0.7 | 2 | 12 | ❌ | 10% | ✅ |

**weekly_cap = 12 是研究甜点 2-4/周 的下限**，对 AI 内容再打 50% 安全折。
**绝对上限**：任何时候不得超过 daily_cap=3 / weekly_cap=20。

### 2.1 其他节奏参数

| 参数 | cold | new | growing | stable |
|---|---|---|---|---|
| hour_window（小时） | — | [10, 22] | [9, 23] | [8, 23] |
| min_gap_min | — | 360 | 240 | 180 |
| max_gap_min | — | 720 | 600 | 540 |
| jitter_min（整点 ±偏移） | — | 20 | 15 | 15 |
| skip_probability（日跳过率） | — | 0.2 | 0.1 | 0.1 |
| collect_quota_multiplier | 0 | 3× | 4× | 5× |

---

## 3. 质量闸门（pre-publish）

pipeline 把每篇候选文章送进闸门；**任一不通过则 skip 并写入 publish_log**。

| 闸门 | 规则 | 默认值 | skip_code |
|---|---|---|---|
| 字数 | 正文字符数 ≥ 阈值 | 800 | `thin_content` |
| 指纹去重 | simhash hamming 距库内最近 200 篇的最小距离 ≥ 阈值 | 10 | `similarity` |
| AI 润色 | `ai_processed=true` | 必需 | `no_ai` |
| 封面图 | `cover_image` 非空 | 可选 | `no_cover` |
| 标签限次 | 该 tag 7 天内已发 < 阈值 | 2 | `tag_repeat` |

所有阈值在 `settings.seo.quality_*`，**进阶模式**可调（普通模式隐藏）。

---

## 4. sitemap 规范

| 项 | 值 | 出处 |
|---|---|---|
| 单文件 URL 上限 | 5000 | 我方取值；Google 硬限 50k，留余量 |
| `<lastmod>` | 只写内容真实变更 | Yoast / Google 官方指引 |
| `<priority>` | **不写** | Google 已忽略（2023 起） |
| `<changefreq>` | **不写** | 同上 |
| 分片结构 | `/sitemap.xml`（index） + `/sitemap-N.xml`（urlset） | Google 标准 |
| robots.txt | 指向 `/sitemap.xml` | 必需 |
| 刷新频率 | 每 60 分钟增量 + 发布时触发 | 我方 |

---

## 5. IndexNow 规范

| 项 | 值 | 出处 |
|---|---|---|
| 参与搜索引擎 | Bing, Yandex, Seznam（Google 不参与） | IndexNow.org |
| Key 部署 | `/{key}.txt` 动态路由返回 key 本身 | 协议要求 |
| 单 URL 重提冷却 | 600 秒 | 官方 FAQ |
| 日 URL 上限 | 500 | 我方设（官方 10k，远低保守） |
| 触发时机 | 文章首次 publish；slug 改 | 我方 |
| 默认状态 | key 已配 → 自动开；key 空 → 自动关 | 我方 |

---

## 6. 阈值出处清单

| 阈值 | 值 | 出处 |
|---|---|---|
| 冷启动 30 天 | 30 | Sandbox Phase 1（0-3 月流量近 0）多源中位数 |
| 新站 90 天 | 90 | Sandbox 研究：1-6 个月，取较保守上限 |
| 稳定期 180 天 | 180 | Semrush：41% 域名 6 月内进 top10 |
| 稳定期 30 篇 | 30 | Topical Authority：主题集群最小规模 |
| daily_cap 3（硬上限） | 3 | 2026 频率研究「中速 niche 甜点 2-4/周」反推 |
| weekly_cap 12（stable） | 12 | 甜点 2-4/周 = 14-28/周，对 AI 打 50% 折 |
| 健康度 0.4 / 0.7 / 1.0 | 0.4/0.7/1.0 | 研究数据拟合：<0.4 = sandbox 压制；0.7 = 健康恢复；1.0 = 稳态表现 |
| 文章最小字数 800 | 800 | Google 对 thin content 判定一般 <500；留 60% 余量 |
| simhash hamming 10 | 10 | 工程经验：64-bit simhash，10-12 是"形近实同"判定区间 |
| 标签 7 天 ≤ 2 篇 | 2 | 反 Doorway Page 信号 + topic cluster 节奏 |
| sitemap 5000/片 | 5000 | Google 硬限 50k；5k 便于增量更新不浪费 crawl budget |
| IndexNow 10 分钟 cooldown | 600s | 官方 FAQ 明确要求 |
| IndexNow 500/天 | 500 | 官方 10k/天硬限；自建站每日发布量远低 |

---

## 7. 校准流程

### 7.1 每季度复核
1. 对比实际 `index_rate` vs 曲线 `expected_index_rate(age_days)`
2. 偏离 ≥ 20%：更新 `expected_index_rate` 函数 + 本文
3. 对比 Semrush/Ahrefs 当期行业研究，决定是否调 cap

### 7.2 Google 政策重大更新时
1. Core update 发布当日评估是否触发保守化（daily_cap - 1）
2. 监控 7 天内流量/收录变化，确认未被打压
3. 政策内容与本文 §0 对照，更新披露

### 7.3 数据驱动觉醒指标
`publish_log` 写入以下指标，dashboard 聚合：
- `actual_index_rate`：实际收录率
- `health_score_trend`：健康度 7/30/90 日移动平均
- `skip_rate`：skipped / scheduled，>30% 说明质量闸太严或采集质量差
- `publish_efficiency`：collected → published 漏斗各阶段留存

---

## 参考文献

- [Google Spam Policies — Scaled Content Abuse](https://developers.google.com/search/docs/essentials/spam-policies)
- [IndexCheckr: Google Indexing Study (16M pages)](https://indexcheckr.com/resources/google-indexing)
- [Semrush: How Long to Rank on Google](https://seo.co/how-long-to-rank/)
- [Google Sandbox Duration Studies](https://www.guangsuan.com/en/post/how-long-is-the-google-sandbox/)
- [2026 Website Posting Frequency Research](https://www.kexworks.com/website-content/website-posting-frequency-2026/)
- [Yoast: lastmod in XML sitemaps — Google and Bing stress](https://yoast.com/lastmod-xml-sitemaps-google-bing/)
- [IndexNow FAQ & Best Practices](https://www.indexnow.org/faq)
- [Topical Authority Burst Strategy Case Study](https://dev.to/topicalhq/case-study-day-0-testing-a-topical-authority-burst-strategy-on-a-brand-new-site-2l98)

---

## 历史版本

| 版本 | 日期 | 变更 | 变更原因 |
|---|---|---|---|
| v1.0 | 2026-04-15 | 初版 | Gui-Tu SEO 模块上线前的基准建立 |
