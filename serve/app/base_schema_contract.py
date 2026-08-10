"""Base legacy schema 兼容合同（WP-01A-01）。

Base 旧库对 V2 是**只读兼容边界**：18 张 Base 表由 legacy bootstrap/migration 拥有，
V2 Alembic 只验证兼容性、绝不修改它们。本模块保存**不可变 canonical 签名**（由权威
dump 的 schema-only 提取生成），并提供纯读取 validator 与 offline 等价的
PostgreSQL precondition SQL。

结果语义（任务 §5.2）：
- 18 表全无 → ``EMPTY``（Base 未 bootstrap，upgrade/downgrade 均 no-op）；
- 18 表全有且签名一致 → ``COMPATIBLE``；
- 存在 1–17 张 → 抛 ``BaseSchemaContractError``（reason ``v2_base_schema_partial``）；
- 18 张全有但签名不符 → 抛 ``BaseSchemaContractError``（reason
  ``v2_base_schema_incompatible``）。

约束：不执行 DDL / commit / rollback；不 log 原始数据库异常；错误只含固定 reason code
与安全的 object identifier（表/列名），不含 DSN / password / Provider message。
比较 schema-qualified 到 ``public``；名称稳定排序；类型经 ``format_type`` 归一化后比较。
"""

from types import MappingProxyType
from typing import Any, Mapping, Sequence

from sqlalchemy import text

PUBLIC_SCHEMA = "public"

# 18 张受检 Base 表（任务 §3 权威清单，与 fixtures manifest 一一对应）。
BASE_LEGACY_TABLES: tuple[str, ...] = (
    "admin_login_logs",
    "admin_operation_logs",
    "admin_user_roles",
    "admin_users",
    "article_keywords",
    "articles",
    "db_backups",
    "dict_items",
    "dicts",
    "files",
    "keywords",
    "menus",
    "messages",
    "publish_log",
    "role_menus",
    "roles",
    "settings",
    "users",
)

REASON_PARTIAL = "v2_base_schema_partial"
REASON_INCOMPATIBLE = "v2_base_schema_incompatible"

# 用于 EXCEPT 行级比较的稳定类型投影（列/类型/nullability 与 PK）。模板在调用时
# 用 ``:t0..tN`` 占位符生成，避免驱动对 `IN (:list)` 的 expanding 依赖差异。
_COLUMNS_SQL_TMPL = """
    SELECT c.relname, a.attname,
           lower(pg_catalog.format_type(a.atttypid, a.atttypmod)),
           NOT a.attnotnull
    FROM pg_catalog.pg_attribute a
    JOIN pg_catalog.pg_class c ON c.oid = a.attrelid
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = :schema AND c.relname IN ({placeholders})
      AND a.attnum > 0 AND NOT a.attisdropped
"""
_PK_SQL_TMPL = """
    SELECT c.relname, a.attname
    FROM pg_catalog.pg_index i
    JOIN pg_catalog.pg_class c ON c.oid = i.indrelid
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_catalog.unnest(i.indkey) WITH ORDINALITY k(attnum, ord) ON true
    JOIN pg_catalog.pg_attribute a ON a.attrelid = c.oid AND a.attnum = k.attnum
    WHERE n.nspname = :schema AND c.relname IN ({placeholders})
      AND i.indisprimary
    ORDER BY c.relname, k.ord
"""


def _freeze(value: Any) -> Any:
    """深冻结 dict→MappingProxyType、list→tuple，保证 canonical 签名不可变。"""
    if isinstance(value, dict):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(v) for v in value)
    return value


def _to_plain(value: Any) -> Any:
    """MappingProxyType/tuple → 可 JSON 化的 dict/list（用于规范化序列化）。"""
    if isinstance(value, Mapping):
        return {k: _to_plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_plain(v) for v in value]
    return value


def _placeholders(count: int, prefix: str = "t") -> str:
    return ", ".join(f":{prefix}{i}" for i in range(count))


def extract_signature(connection, tables: Sequence[str] = BASE_LEGACY_TABLES,
                      schema: str = PUBLIC_SCHEMA) -> dict:
    """只读提取 ``public`` 下指定表的规范化签名；不 DDL / commit / rollback。

    返回 ``{table: {"columns": {col: {"type": str, "nullable": bool}},
    "primary_key": tuple[str, ...]}}``，只含当前存在的表。类型经
    ``format_type`` 归一化（小写、去空白），因此与 DB 无关、跨库可复现。
    """
    names = list(tables)
    placeholders = _placeholders(len(names))
    params = {f"t{i}": n for i, n in enumerate(names)}
    params["schema"] = schema

    columns: dict[str, dict] = {t: {} for t in names}
    pks: dict[str, list] = {t: [] for t in names}

    for tbl, col, typ, nullable in connection.execute(
        text(_COLUMNS_SQL_TMPL.format(placeholders=placeholders)), params
    ):
        columns[tbl][col] = {"type": _normalize_type(typ), "nullable": bool(nullable)}
    for tbl, col in connection.execute(
        text(_PK_SQL_TMPL.format(placeholders=placeholders)), params
    ):
        pks[tbl].append(col)

    return {
        tbl: {"columns": columns[tbl], "primary_key": tuple(pks[tbl])}
        for tbl in names
        if columns[tbl]
    }


def _normalize_type(raw: str) -> str:
    """类型归一化：小写 + 折叠连续空白，消除驱动/格式差异。"""
    return " ".join(str(raw).lower().split())


class BaseSchemaContractError(RuntimeError):
    """固定 reason code + 安全 object identifier；不含 DSN / Provider message。"""

    def __init__(self, reason_code: str, detail: str | None = None):
        self.reason_code = reason_code
        message = reason_code if not detail else f"{reason_code}: {detail}"
        super().__init__(message)


class BaseSchemaResult:
    __slots__ = ("status", "table_count")

    def __init__(self, status: str, table_count: int):
        self.status = status
        self.table_count = table_count

    def __eq__(self, other):
        return (
            isinstance(other, BaseSchemaResult)
            and self.status == other.status
            and self.table_count == other.table_count
        )

    def __repr__(self):
        return f"BaseSchemaResult(status={self.status!r}, table_count={self.table_count})"


def _first_mismatch(existing: Mapping[str, Mapping]) -> str | None:
    """定位第一个签名差异，返回安全 object identifier（不含 DSN）。"""
    for tbl in BASE_LEGACY_TABLES:
        expected = CANONICAL_SIGNATURE[tbl]
        got = existing[tbl]
        if got == expected:
            continue
        if got["columns"] != expected["columns"]:
            for col in sorted(set(got["columns"]) | set(expected["columns"])):
                if got["columns"].get(col) != expected["columns"].get(col):
                    return f"table={tbl} column={col}"
        if got["primary_key"] != expected["primary_key"]:
            return f"table={tbl} primary_key"
        return f"table={tbl}"
    return None


def validate_base_schema(connection) -> BaseSchemaResult:
    """返回 ``EMPTY`` 或 ``COMPATIBLE``；partial/incompatible 抛错。

    - 0 张 Base 表 → ``EMPTY``；
    - 1–17 张 → ``BaseSchemaContractError``（``v2_base_schema_partial``）；
    - 18 张全有但签名不符 → ``BaseSchemaContractError``（``v2_base_schema_incompatible``）；
    - 18 张全有且签名一致 → ``COMPATIBLE``。

    只读：本函数只发 SELECT，不 commit / rollback / DDL。
    """
    existing = extract_signature(connection)
    if not existing:
        return BaseSchemaResult("EMPTY", 0)

    present = set(existing)
    expected = set(BASE_LEGACY_TABLES)
    if present != expected:
        detail = []
        missing = sorted(expected - present)
        if missing:
            detail.append("missing=" + ",".join(missing))
        extra = sorted(present - expected)
        if extra:
            detail.append("extra=" + ",".join(extra))
        raise BaseSchemaContractError(REASON_PARTIAL, "; ".join(detail))

    mismatch = _first_mismatch(existing)
    if mismatch is not None:
        raise BaseSchemaContractError(REASON_INCOMPATIBLE, mismatch)
    return BaseSchemaResult("COMPATIBLE", 18)


def offline_precondition_sql() -> str:
    """生成等价于在线 validator 语义的 PostgreSQL precondition（DO 块）。

    在应用 offline SQL 时执行：0 张表 no-op、1–17 张表 ``v2_base_schema_partial``、
    18 张全有但签名不符 ``v2_base_schema_incompatible``。基于
    ``CANONICAL_SIGNATURE`` 单源生成，不含密码/DSN。
    """
    expected_tables = ", ".join(f"'{t}'" for t in BASE_LEGACY_TABLES)

    col_rows = []
    pk_rows = []
    for tbl in BASE_LEGACY_TABLES:
        sig = CANONICAL_SIGNATURE[tbl]
        for col, info in sig["columns"].items():
            col_rows.append(
                f"('{tbl}', '{col}', '{info['type']}', {_sql_bool(not info['nullable'])})"
            )
        # 主键列按 1-based ordinal 展开（与 online extract_signature 保留 tuple 顺序一致，
        # 与 catalog 侧 unnest(...) WITH ORDINALITY 的 ord 对齐）。复合主键的列顺序必须
        # 与 canonical 完全一致，交换顺序即 incompatible。
        for ord_, col in enumerate(sig["primary_key"], start=1):
            pk_rows.append(f"('{tbl}', '{col}', {ord_})")

    col_values = ",\n".join(col_rows)
    pk_values = ",\n".join(pk_rows)

    return f"""DO $v2_base_precondition$
DECLARE
    present_count integer;
    expected_tbls text[] := ARRAY[{expected_tables}];
BEGIN
    SELECT count(*) INTO present_count
    FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relname = ANY(expected_tbls);

    IF present_count = 0 THEN
        RETURN;  -- literal-empty：Base 未 bootstrap，等价 no-op
    END IF;

    IF present_count < {len(BASE_LEGACY_TABLES)} THEN
        RAISE EXCEPTION '{REASON_PARTIAL}';
    END IF;

    IF EXISTS (
        (SELECT e.tbl, e.col, e.typ, e.nonnull FROM (VALUES
{col_values}
        ) AS e(tbl, col, typ, nonnull)
        EXCEPT
        SELECT c.relname::text, a.attname::text,
               lower(pg_catalog.format_type(a.atttypid, a.atttypmod)),
               a.attnotnull
        FROM pg_catalog.pg_attribute a
        JOIN pg_catalog.pg_class c ON c.oid = a.attrelid
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relname = ANY(expected_tbls)
          AND a.attnum > 0 AND NOT a.attisdropped)
        UNION
        (SELECT c.relname::text, a.attname::text,
               lower(pg_catalog.format_type(a.atttypid, a.atttypmod)),
               a.attnotnull
        FROM pg_catalog.pg_attribute a
        JOIN pg_catalog.pg_class c ON c.oid = a.attrelid
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relname = ANY(expected_tbls)
          AND a.attnum > 0 AND NOT a.attisdropped
        EXCEPT
        SELECT e.tbl, e.col, e.typ, e.nonnull FROM (VALUES
{col_values}
        ) AS e(tbl, col, typ, nonnull))
    ) THEN
        RAISE EXCEPTION '{REASON_INCOMPATIBLE}';
    END IF;

    IF EXISTS (
        (SELECT e.tbl, e.col, e.ord FROM (VALUES
{pk_values}
        ) AS e(tbl, col, ord)
        EXCEPT
        SELECT c.relname::text, a.attname::text, k.ord::integer
        FROM pg_catalog.pg_index i
        JOIN pg_catalog.pg_class c ON c.oid = i.indrelid
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_catalog.unnest(i.indkey) WITH ORDINALITY k(attnum, ord) ON true
        JOIN pg_catalog.pg_attribute a ON a.attrelid = c.oid AND a.attnum = k.attnum
        WHERE n.nspname = 'public' AND i.indisprimary AND c.relname = ANY(expected_tbls))
        UNION
        (SELECT c.relname::text, a.attname::text, k.ord::integer
        FROM pg_catalog.pg_index i
        JOIN pg_catalog.pg_class c ON c.oid = i.indrelid
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_catalog.unnest(i.indkey) WITH ORDINALITY k(attnum, ord) ON true
        JOIN pg_catalog.pg_attribute a ON a.attrelid = c.oid AND a.attnum = k.attnum
        WHERE n.nspname = 'public' AND i.indisprimary AND c.relname = ANY(expected_tbls)
        EXCEPT
        SELECT e.tbl, e.col, e.ord FROM (VALUES
{pk_values}
        ) AS e(tbl, col, ord))
    ) THEN
        RAISE EXCEPTION '{REASON_INCOMPATIBLE}';
    END IF;
END
$v2_base_precondition$;"""


def _sql_bool(value: bool) -> str:
    return "true" if value else "false"


def canonical_signature_sha256() -> str:
    """规范化 schema 签名 SHA-256（确定性 JSON 序列化，供 manifest/测试复算）。"""
    import hashlib
    import json

    payload = json.dumps(
        _to_plain(CANONICAL_SIGNATURE),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


# ---------------------------------------------------------------------------
# Canonical 签名：由权威 dump 的 schema-only 提取生成（见
# serve/docs/manifests/wp-01a-01-base-schema-contract.md §5.1），冻结后不可改。
# 结构：{table: {"columns": {col: {"type": <format_type 归一化>, "nullable": bool}},
#                 "primary_key": tuple[str, ...]}}
# ---------------------------------------------------------------------------
CANONICAL_SIGNATURE = _freeze({
    'admin_login_logs': {
        'columns': {
            'id': {
                'type': 'integer',
                'nullable': False
            },
            'user_id': {
                'type': 'integer',
                'nullable': False
            },
            'username': {
                'type': 'character varying(50)',
                'nullable': False
            },
            'ip': {
                'type': 'character varying(50)',
                'nullable': True
            },
            'user_agent': {
                'type': 'character varying(500)',
                'nullable': True
            },
            'status': {
                'type': 'smallint',
                'nullable': False
            },
            'remark': {
                'type': 'character varying(255)',
                'nullable': True
            },
            'created_at': {
                'type': 'timestamp without time zone',
                'nullable': False
            }
        },
        'primary_key': (
        'id',
        )
    },
    'admin_operation_logs': {
        'columns': {
            'id': {
                'type': 'integer',
                'nullable': False
            },
            'user_id': {
                'type': 'integer',
                'nullable': False
            },
            'username': {
                'type': 'character varying(50)',
                'nullable': False
            },
            'module': {
                'type': 'character varying(100)',
                'nullable': False
            },
            'action': {
                'type': 'character varying(100)',
                'nullable': False
            },
            'method': {
                'type': 'character varying(10)',
                'nullable': False
            },
            'url': {
                'type': 'character varying(500)',
                'nullable': False
            },
            'params': {
                'type': 'text',
                'nullable': True
            },
            'ip': {
                'type': 'character varying(50)',
                'nullable': True
            },
            'user_agent': {
                'type': 'character varying(500)',
                'nullable': True
            },
            'status_code': {
                'type': 'integer',
                'nullable': False
            },
            'duration': {
                'type': 'integer',
                'nullable': False
            },
            'created_at': {
                'type': 'timestamp without time zone',
                'nullable': False
            }
        },
        'primary_key': (
        'id',
        )
    },
    'admin_user_roles': {
        'columns': {
            'admin_user_id': {
                'type': 'integer',
                'nullable': False
            },
            'role_id': {
                'type': 'integer',
                'nullable': False
            }
        },
        'primary_key': (
        'admin_user_id',
        'role_id',
        )
    },
    'admin_users': {
        'columns': {
            'id': {
                'type': 'integer',
                'nullable': False
            },
            'username': {
                'type': 'character varying(50)',
                'nullable': False
            },
            'password': {
                'type': 'character varying(255)',
                'nullable': False
            },
            'nickname': {
                'type': 'character varying(50)',
                'nullable': True
            },
            'avatar': {
                'type': 'character varying(255)',
                'nullable': True
            },
            'email': {
                'type': 'character varying(100)',
                'nullable': True
            },
            'phone': {
                'type': 'character varying(20)',
                'nullable': True
            },
            'status': {
                'type': 'smallint',
                'nullable': False
            },
            'is_super_admin': {
                'type': 'boolean',
                'nullable': False
            },
            'token_version': {
                'type': 'integer',
                'nullable': False
            },
            'last_login_at': {
                'type': 'timestamp without time zone',
                'nullable': True
            },
            'last_login_ip': {
                'type': 'character varying(50)',
                'nullable': True
            },
            'created_at': {
                'type': 'timestamp without time zone',
                'nullable': False
            },
            'updated_at': {
                'type': 'timestamp without time zone',
                'nullable': False
            },
            'password_changed_at': {
                'type': 'timestamp without time zone',
                'nullable': True
            }
        },
        'primary_key': (
        'id',
        )
    },
    'article_keywords': {
        'columns': {
            'article_id': {
                'type': 'bigint',
                'nullable': False
            },
            'keyword_id': {
                'type': 'bigint',
                'nullable': False
            },
            'is_primary': {
                'type': 'boolean',
                'nullable': False
            },
            'created_at': {
                'type': 'timestamp without time zone',
                'nullable': False
            }
        },
        'primary_key': (
        'article_id',
        'keyword_id',
        )
    },
    'articles': {
        'columns': {
            'id': {
                'type': 'integer',
                'nullable': False
            },
            'title': {
                'type': 'character varying(200)',
                'nullable': False
            },
            'slug': {
                'type': 'character varying(200)',
                'nullable': True
            },
            'summary': {
                'type': 'character varying(500)',
                'nullable': True
            },
            'excerpt': {
                'type': 'text',
                'nullable': True
            },
            'content': {
                'type': 'text',
                'nullable': False
            },
            'cover_image': {
                'type': 'character varying(500)',
                'nullable': True
            },
            'author_id': {
                'type': 'integer',
                'nullable': True
            },
            'view_count': {
                'type': 'integer',
                'nullable': False
            },
            'is_pinned': {
                'type': 'boolean',
                'nullable': False
            },
            'sort': {
                'type': 'integer',
                'nullable': False
            },
            'source': {
                'type': 'smallint',
                'nullable': False
            },
            'source_url': {
                'type': 'character varying(500)',
                'nullable': True
            },
            'raw_content': {
                'type': 'text',
                'nullable': True
            },
            'ai_processed': {
                'type': 'boolean',
                'nullable': False
            },
            'status': {
                'type': 'smallint',
                'nullable': False
            },
            'published_at': {
                'type': 'timestamp without time zone',
                'nullable': True
            },
            'simhash': {
                'type': 'bigint',
                'nullable': True
            },
            'slug_history': {
                'type': 'jsonb',
                'nullable': False
            },
            'deleted_at': {
                'type': 'timestamp without time zone',
                'nullable': True
            },
            'scheduled_at': {
                'type': 'timestamp without time zone',
                'nullable': True
            },
            'retry_count': {
                'type': 'smallint',
                'nullable': False
            },
            'last_publish_error': {
                'type': 'text',
                'nullable': True
            },
            'created_at': {
                'type': 'timestamp without time zone',
                'nullable': False
            },
            'updated_at': {
                'type': 'timestamp without time zone',
                'nullable': False
            }
        },
        'primary_key': (
        'id',
        )
    },
    'db_backups': {
        'columns': {
            'id': {
                'type': 'integer',
                'nullable': False
            },
            'filename': {
                'type': 'character varying(255)',
                'nullable': False
            },
            'file_size': {
                'type': 'bigint',
                'nullable': False
            },
            'status': {
                'type': 'character varying(16)',
                'nullable': False
            },
            'started_at': {
                'type': 'timestamp without time zone',
                'nullable': True
            },
            'finished_at': {
                'type': 'timestamp without time zone',
                'nullable': True
            },
            'error_msg': {
                'type': 'character varying(500)',
                'nullable': True
            },
            'created_at': {
                'type': 'timestamp without time zone',
                'nullable': False
            }
        },
        'primary_key': (
        'id',
        )
    },
    'dict_items': {
        'columns': {
            'id': {
                'type': 'integer',
                'nullable': False
            },
            'dict_id': {
                'type': 'integer',
                'nullable': False
            },
            'value': {
                'type': 'character varying(100)',
                'nullable': False
            },
            'label': {
                'type': 'character varying(100)',
                'nullable': False
            },
            'sort': {
                'type': 'integer',
                'nullable': False
            },
            'status': {
                'type': 'smallint',
                'nullable': False
            },
            'created_at': {
                'type': 'timestamp without time zone',
                'nullable': False
            }
        },
        'primary_key': (
        'id',
        )
    },
    'dicts': {
        'columns': {
            'id': {
                'type': 'integer',
                'nullable': False
            },
            'type_name': {
                'type': 'character varying(50)',
                'nullable': False
            },
            'description': {
                'type': 'character varying(200)',
                'nullable': True
            },
            'status': {
                'type': 'smallint',
                'nullable': False
            },
            'created_at': {
                'type': 'timestamp without time zone',
                'nullable': False
            },
            'updated_at': {
                'type': 'timestamp without time zone',
                'nullable': False
            }
        },
        'primary_key': (
        'id',
        )
    },
    'files': {
        'columns': {
            'id': {
                'type': 'integer',
                'nullable': False
            },
            'name': {
                'type': 'character varying(200)',
                'nullable': False
            },
            'original_name': {
                'type': 'character varying(200)',
                'nullable': False
            },
            'path': {
                'type': 'character varying(500)',
                'nullable': False
            },
            'url': {
                'type': 'character varying(500)',
                'nullable': False
            },
            'platform': {
                'type': 'character varying(50)',
                'nullable': False
            },
            'mime_type': {
                'type': 'character varying(100)',
                'nullable': True
            },
            'size': {
                'type': 'integer',
                'nullable': False
            },
            'ext': {
                'type': 'character varying(20)',
                'nullable': True
            },
            'is_private': {
                'type': 'boolean',
                'nullable': False
            },
            'user_id': {
                'type': 'integer',
                'nullable': True
            },
            'category': {
                'type': 'character varying(50)',
                'nullable': True
            },
            'created_at': {
                'type': 'timestamp without time zone',
                'nullable': False
            }
        },
        'primary_key': (
        'id',
        )
    },
    'keywords': {
        'columns': {
            'id': {
                'type': 'bigint',
                'nullable': False
            },
            'keyword': {
                'type': 'character varying(200)',
                'nullable': False
            },
            'keyword_norm': {
                'type': 'character varying(200)',
                'nullable': False
            },
            'slug': {
                'type': 'character varying(200)',
                'nullable': True
            },
            'stage': {
                'type': 'character varying(16)',
                'nullable': False
            },
            'review_status': {
                'type': 'character varying(16)',
                'nullable': False
            },
            'source_code': {
                'type': 'character varying(32)',
                'nullable': False
            },
            'seed_keyword': {
                'type': 'character varying(200)',
                'nullable': True
            },
            'expanded_as_seed_at': {
                'type': 'timestamp without time zone',
                'nullable': True
            },
            'fetched_at': {
                'type': 'timestamp without time zone',
                'nullable': False
            },
            'metrics_json': {
                'type': 'jsonb',
                'nullable': False
            },
            'ai_review_json': {
                'type': 'jsonb',
                'nullable': True
            },
            'color': {
                'type': 'character varying(20)',
                'nullable': True
            },
            'description': {
                'type': 'character varying(200)',
                'nullable': True
            },
            'sort': {
                'type': 'integer',
                'nullable': False
            },
            'article_count': {
                'type': 'integer',
                'nullable': False
            },
            'created_at': {
                'type': 'timestamp without time zone',
                'nullable': False
            },
            'updated_at': {
                'type': 'timestamp without time zone',
                'nullable': False
            }
        },
        'primary_key': (
        'id',
        )
    },
    'menus': {
        'columns': {
            'id': {
                'type': 'integer',
                'nullable': False
            },
            'parent_id': {
                'type': 'integer',
                'nullable': False
            },
            'type': {
                'type': 'smallint',
                'nullable': False
            },
            'slug': {
                'type': 'character varying(50)',
                'nullable': False
            },
            'label': {
                'type': 'character varying(50)',
                'nullable': False
            },
            'icon': {
                'type': 'character varying(100)',
                'nullable': True
            },
            'path': {
                'type': 'character varying(200)',
                'nullable': True
            },
            'template_path': {
                'type': 'character varying(200)',
                'nullable': True
            },
            'redirect': {
                'type': 'character varying(200)',
                'nullable': True
            },
            'perms': {
                'type': 'character varying(100)',
                'nullable': True
            },
            'link': {
                'type': 'character varying(500)',
                'nullable': True
            },
            'link_target': {
                'type': 'character varying(10)',
                'nullable': True
            },
            'is_cache': {
                'type': 'boolean',
                'nullable': False
            },
            'is_affix': {
                'type': 'boolean',
                'nullable': False
            },
            'is_visible': {
                'type': 'boolean',
                'nullable': False
            },
            'badge': {
                'type': 'character varying(20)',
                'nullable': True
            },
            'sort': {
                'type': 'integer',
                'nullable': False
            },
            'status': {
                'type': 'smallint',
                'nullable': False
            },
            'remark': {
                'type': 'character varying(255)',
                'nullable': True
            },
            'created_at': {
                'type': 'timestamp without time zone',
                'nullable': False
            },
            'updated_at': {
                'type': 'timestamp without time zone',
                'nullable': False
            }
        },
        'primary_key': (
        'id',
        )
    },
    'messages': {
        'columns': {
            'id': {
                'type': 'integer',
                'nullable': False
            },
            'user_id': {
                'type': 'integer',
                'nullable': False
            },
            'title': {
                'type': 'character varying(200)',
                'nullable': False
            },
            'content': {
                'type': 'text',
                'nullable': True
            },
            'type': {
                'type': 'smallint',
                'nullable': False
            },
            'is_read': {
                'type': 'boolean',
                'nullable': False
            },
            'sender_id': {
                'type': 'integer',
                'nullable': True
            },
            'sender_name': {
                'type': 'character varying(50)',
                'nullable': True
            },
            'created_at': {
                'type': 'timestamp without time zone',
                'nullable': False
            },
            'updated_at': {
                'type': 'timestamp without time zone',
                'nullable': False
            }
        },
        'primary_key': (
        'id',
        )
    },
    'publish_log': {
        'columns': {
            'id': {
                'type': 'bigint',
                'nullable': False
            },
            'action': {
                'type': 'character varying(32)',
                'nullable': False
            },
            'level': {
                'type': 'character varying(8)',
                'nullable': False
            },
            'article_id': {
                'type': 'integer',
                'nullable': True
            },
            'msg': {
                'type': 'text',
                'nullable': True
            },
            'payload': {
                'type': 'jsonb',
                'nullable': True
            },
            'created_at': {
                'type': 'timestamp without time zone',
                'nullable': False
            }
        },
        'primary_key': (
        'id',
        )
    },
    'role_menus': {
        'columns': {
            'role_id': {
                'type': 'integer',
                'nullable': False
            },
            'menu_id': {
                'type': 'integer',
                'nullable': False
            }
        },
        'primary_key': (
        'role_id',
        'menu_id',
        )
    },
    'roles': {
        'columns': {
            'id': {
                'type': 'integer',
                'nullable': False
            },
            'name': {
                'type': 'character varying(50)',
                'nullable': False
            },
            'label': {
                'type': 'character varying(50)',
                'nullable': False
            },
            'remark': {
                'type': 'character varying(255)',
                'nullable': True
            },
            'sort': {
                'type': 'integer',
                'nullable': False
            },
            'status': {
                'type': 'smallint',
                'nullable': False
            },
            'created_at': {
                'type': 'timestamp without time zone',
                'nullable': False
            },
            'updated_at': {
                'type': 'timestamp without time zone',
                'nullable': False
            }
        },
        'primary_key': (
        'id',
        )
    },
    'settings': {
        'columns': {
            'id': {
                'type': 'integer',
                'nullable': False
            },
            'category': {
                'type': 'character varying(50)',
                'nullable': False
            },
            'name': {
                'type': 'character varying(100)',
                'nullable': False
            },
            'label': {
                'type': 'character varying(100)',
                'nullable': True
            },
            'value': {
                'type': 'text',
                'nullable': False
            },
            'remark': {
                'type': 'character varying(255)',
                'nullable': True
            },
            'created_at': {
                'type': 'timestamp without time zone',
                'nullable': False
            },
            'updated_at': {
                'type': 'timestamp without time zone',
                'nullable': False
            }
        },
        'primary_key': (
        'id',
        )
    },
    'users': {
        'columns': {
            'id': {
                'type': 'integer',
                'nullable': False
            },
            'username': {
                'type': 'character varying(50)',
                'nullable': False
            },
            'password': {
                'type': 'character varying(255)',
                'nullable': False
            },
            'nickname': {
                'type': 'character varying(50)',
                'nullable': True
            },
            'avatar': {
                'type': 'character varying(255)',
                'nullable': True
            },
            'email': {
                'type': 'character varying(100)',
                'nullable': True
            },
            'phone': {
                'type': 'character varying(20)',
                'nullable': True
            },
            'status': {
                'type': 'smallint',
                'nullable': False
            },
            'token_version': {
                'type': 'integer',
                'nullable': False
            },
            'last_login_at': {
                'type': 'timestamp without time zone',
                'nullable': True
            },
            'last_login_ip': {
                'type': 'character varying(50)',
                'nullable': True
            },
            'created_at': {
                'type': 'timestamp without time zone',
                'nullable': False
            },
            'updated_at': {
                'type': 'timestamp without time zone',
                'nullable': False
            }
        },
        'primary_key': (
        'id',
        )
    }
})
