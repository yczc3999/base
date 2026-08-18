"""Admin 路由 Manifest — /api/admin 全量影子 Manifest。

划分为：
- `admin_public`：公开端点（captcha/login/refreshToken/setting/site）
- `admin`：require_admin 保护（access=ADMIN）
- `private_file`：/api/file/{file_id} 隐私文件代理

设计文档 §7.2 的权限映射原样迁移；未列入权限表的 custom route
只保留 require_admin，不新增权限门。
"""
from __future__ import annotations

from app.controllers.admin import article as admin_article
from app.controllers.admin import cache as admin_cache
from app.controllers.admin import client_user as admin_client_user
from app.controllers.admin import dashboard as admin_dashboard
from app.controllers.admin import db_backup as admin_db_backup
from app.controllers.admin import export as admin_export
from app.controllers.admin import file as admin_file
from app.controllers.admin import import_api as admin_import
from app.controllers.admin import keyword as admin_keyword
from app.controllers.admin import menu as admin_menu
from app.controllers.admin import message as admin_message
from app.controllers.admin import migration as admin_migration
from app.controllers.admin import monitor as admin_monitor
from app.controllers.admin import role as admin_role
from app.controllers.admin import seo as admin_seo
from app.controllers.admin import session as admin_session
from app.controllers.admin import setting as admin_setting
from app.controllers.admin import task_monitor as admin_task_monitor
from app.controllers.admin import trash as admin_trash
from app.controllers.admin import user as admin_user
from app.deps import require_admin
from app.logics.admin_login_log import admin_login_log_logic
from app.logics.admin_operation_log import admin_operation_log_logic
from app.logics.admin_user import admin_user_logic
from app.logics.article import article_logic
from app.logics.db_backup import db_backup_logic
from app.logics.dict import dict_item_logic, dict_logic
from app.logics.file import file_logic
from app.logics.keyword import keyword_logic
from app.logics.menu import menu_logic
from app.logics.message import message_logic
from app.logics.publish_log import publish_log_logic
from app.logics.role import role_logic
from app.logics.user import user_logic
from app.routes.registry import RouteRegistry
from app.routes.types import RouteAccess


def register_admin_routes(routes: RouteRegistry) -> None:
    admin_public = routes.group(
        prefix="/api/admin", name="admin.", access=RouteAccess.PUBLIC
    )
    admin = admin_public.group(middleware=[require_admin], access=RouteAccess.ADMIN)

    _register_public(admin_public)
    _register_crud(admin)
    _register_user(admin)
    _register_setting(admin)
    _register_menu_role(admin)
    _register_message_file(admin)
    _register_dashboard_export(admin)
    _register_article_keyword(admin)
    _register_seo(admin)
    _register_client_user_task(admin)
    _register_db_migration_monitor(admin)
    _register_import_session_cache(admin)
    _register_trash(admin)
    _register_private_file(routes)


def _register_public(admin_public) -> None:
    admin_public.get("/user/captcha", admin_user.get_captcha).name("user.captcha")
    admin_public.post("/user/login", admin_user.login).name("user.login")
    admin_public.post("/user/refreshToken", admin_user.refresh_token).name(
        "user.refreshToken"
    )
    admin_public.get("/setting/site", admin_setting.get_site_info).name(
        "setting.site"
    )


def _register_crud(admin) -> None:
    admin.crud(
        "/user",
        admin_user_logic,
        tags=["admin-user"],
        permissions="admin:user",
        auth_dep=require_admin,
        perms_prefix="admin:user",
    )
    admin.crud(
        "/operationLog",
        admin_operation_log_logic,
        tags=["admin-log"],
        permissions="admin:log:operation",
        auth_dep=require_admin,
        perms_prefix="admin:log:operation",
    )
    admin.crud(
        "/loginLog",
        admin_login_log_logic,
        tags=["admin-log"],
        permissions="admin:log:login",
        auth_dep=require_admin,
        perms_prefix="admin:log:login",
    )
    admin.crud(
        "/menu",
        menu_logic,
        tags=["admin-menu"],
        permissions="admin:menu",
        auth_dep=require_admin,
        perms_prefix="admin:menu",
    )
    admin.crud(
        "/role",
        role_logic,
        tags=["admin-role"],
        permissions="admin:role",
        auth_dep=require_admin,
        perms_prefix="admin:role",
    )
    admin.crud(
        "/message",
        message_logic,
        tags=["admin-message"],
        permissions="admin:message",
        auth_dep=require_admin,
        perms_prefix="admin:message",
    )
    admin.crud(
        "/file",
        file_logic,
        tags=["admin-file"],
        permissions="admin:file",
        auth_dep=require_admin,
        perms_prefix="admin:file",
    )
    admin.crud(
        "/article",
        article_logic,
        tags=["admin-article"],
        permissions="admin:article",
        auth_dep=require_admin,
        perms_prefix="admin:article",
    )
    admin.crud(
        "/keyword",
        keyword_logic,
        tags=["admin-keyword"],
        permissions="admin:keyword",
        auth_dep=require_admin,
        perms_prefix="admin:keyword",
    )
    admin.crud(
        "/publish_log",
        publish_log_logic,
        tags=["admin-seo"],
        permissions="admin:seo",
        auth_dep=require_admin,
        perms_prefix="admin:seo",
    )
    admin.crud(
        "/dict",
        dict_logic,
        tags=["admin-dict"],
        permissions="admin:dict",
        auth_dep=require_admin,
        perms_prefix="admin:dict",
    )
    admin.crud(
        "/dict_item",
        dict_item_logic,
        tags=["admin-dict"],
        permissions="admin:dict",
        auth_dep=require_admin,
        perms_prefix="admin:dict",
    )
    admin.crud(
        "/client_user",
        user_logic,
        tags=["admin-client-user"],
        permissions="admin:client_user",
        auth_dep=require_admin,
        perms_prefix="admin:client_user",
    )
    admin.crud(
        "/db_backup",
        db_backup_logic,
        tags=["admin-db-backup"],
        permissions="admin:db_backup",
        auth_dep=require_admin,
        perms_prefix="admin:db_backup",
    )


def _register_user(admin) -> None:
    admin.get("/user/info", admin_user.user_info).name("user.info")
    admin.post("/user/changePassword", admin_user.change_password).name(
        "user.changePassword"
    )
    admin.post("/user/logout", admin_user.logout).name("user.logout")
    admin.get("/user/menus", admin_user.user_menus).name("user.menus")
    admin.get("/user/roleIds", admin_user.user_role_ids).permission(
        "admin:user:list"
    ).name("user.roleIds")
    admin.post("/user/assignRoles", admin_user.assign_roles).permission(
        "admin:user:assignRole"
    ).name("user.assignRoles")
    admin.post("/user/updateProfile", admin_user.update_profile).name(
        "user.updateProfile"
    )


def _register_setting(admin) -> None:
    admin.get("/setting/get", admin_setting.get_settings).permission(
        "admin:setting:get"
    ).name("setting.get")
    admin.post("/setting/set", admin_setting.set_settings).permission(
        "admin:setting:set"
    ).name("setting.set")
    admin.get(
        "/setting/ai/provider-defaults", admin_setting.ai_provider_defaults
    ).name("setting.ai_provider_defaults")
    admin.get(
        "/setting/ai-review/defaults", admin_setting.ai_review_defaults
    ).name("setting.ai_review_defaults")
    admin.post(
        "/setting/ai-review/generate", admin_setting.generate_review_prompt
    ).name("setting.generate_review_prompt")
    admin.post("/setting/ai/test", admin_setting.ai_test_connection).name(
        "setting.ai_test_connection"
    )


def _register_menu_role(admin) -> None:
    admin.get("/menu/tree", admin_menu.menu_tree).permission(
        "admin:menu:list"
    ).name("menu.tree")
    admin.get("/role/menuIds", admin_role.role_menu_ids).permission(
        "admin:role:list"
    ).name("role.menuIds")
    admin.post("/role/assignMenus", admin_role.assign_menus).permission(
        "admin:role:assignMenu"
    ).name("role.assignMenus")


def _register_message_file(admin) -> None:
    admin.get("/message/unreadCount", admin_message.unread_count).name(
        "message.unreadCount"
    )
    admin.post("/message/markRead", admin_message.mark_read).permission(
        "admin:message:read"
    ).name("message.markRead")
    admin.post("/file/upload", admin_file.upload_file).name("file.upload")
    admin.post("/file/uploadImage", admin_file.upload_image).name("file.uploadImage")
    admin.post("/file/batchDelete", admin_file.batch_delete).name("file.batchDelete")


def _register_dashboard_export(admin) -> None:
    admin.get("/dashboard/stats", admin_dashboard.dashboard_stats).name(
        "dashboard.stats"
    )
    admin.get("/dashboard/system", admin_dashboard.dashboard_system).name(
        "dashboard.system"
    )
    admin.get("/dashboard/recent", admin_dashboard.dashboard_recent).name(
        "dashboard.recent"
    )
    admin.get("/export/progress", admin_export.export_progress).tags(
        "export"
    ).name("export.progress")
    admin.get("/export/download", admin_export.export_download).tags(
        "export"
    ).name("export.download")


def _register_article_keyword(admin) -> None:
    admin.post("/article/ai-generate", admin_article.ai_generate).tags(
        "admin-article"
    ).name("article.ai-generate")
    admin.post("/article/collect-stream", admin_article.collect_stream).tags(
        "admin-article"
    ).name("article.collect-stream")
    admin.post(
        "/article/gen-from-tags-stream", admin_article.gen_from_tags_stream
    ).tags("admin-article").name("article.gen-from-tags-stream")
    admin.post("/article/ai-rewrite-stream", admin_article.ai_rewrite_stream).tags(
        "admin-article"
    ).name("article.ai-rewrite-stream")
    admin.get("/article/collect-stats", admin_article.collect_stats).tags(
        "admin-article"
    ).name("article.collect-stats")
    admin.post("/keyword/harvest-stream", admin_keyword.harvest_stream).tags(
        "admin-keyword"
    ).name("keyword.harvest-stream")
    admin.post(
        "/keyword/poll-harvest-stream", admin_keyword.poll_harvest_stream
    ).tags("admin-keyword").name("keyword.poll-harvest-stream")
    admin.post("/keyword/bulk-approve", admin_keyword.bulk_approve).tags(
        "admin-keyword"
    ).name("keyword.bulk-approve")
    admin.post("/keyword/bulk-reject", admin_keyword.bulk_reject).tags(
        "admin-keyword"
    ).name("keyword.bulk-reject")
    admin.post("/keyword/bulk-stage", admin_keyword.bulk_set_stage).tags(
        "admin-keyword"
    ).name("keyword.bulk-stage")
    admin.get("/keyword/stats", admin_keyword.keyword_stats).tags(
        "admin-keyword"
    ).name("keyword.stats")
    admin.post("/keyword/ai-seeds", admin_keyword.ai_seed_suggest).tags(
        "admin-keyword"
    ).name("keyword.ai-seeds")
    admin.post("/keyword/ai-review-stream", admin_keyword.ai_review_stream).tags(
        "admin-keyword"
    ).name("keyword.ai-review-stream")


def _register_seo(admin) -> None:
    admin.get("/seo/dashboard", admin_seo.dashboard).tags("admin-seo").name(
        "seo.dashboard"
    )
    admin.post("/seo/toggle", admin_seo.toggle_seo).tags("admin-seo").name(
        "seo.toggle"
    )
    admin.post("/seo/kill-switch", admin_seo.set_kill_switch).tags(
        "admin-seo"
    ).name("seo.kill-switch")
    admin.post("/seo/sitemap/rebuild", admin_seo.rebuild_sitemap).tags(
        "admin-seo"
    ).name("seo.sitemap.rebuild")
    admin.get("/seo/sitemap/files", admin_seo.list_sitemap_files).tags(
        "admin-seo"
    ).name("seo.sitemap.files")
    admin.post("/seo/phase/recompute", admin_seo.recompute_phase).tags(
        "admin-seo"
    ).name("seo.phase.recompute")
    admin.post("/seo/indexnow/test", admin_seo.indexnow_test).tags(
        "admin-seo"
    ).name("seo.indexnow.test")
    admin.post("/seo/run-now", admin_seo.run_pipeline_now).tags("admin-seo").name(
        "seo.run-now"
    )


def _register_client_user_task(admin) -> None:
    admin.post(
        "/client_user/resetPassword", admin_client_user.reset_password
    ).permission("admin:client_user:edit").name("client_user.resetPassword")
    admin.post("/client_user/kick", admin_client_user.kick).permission(
        "admin:client_user:edit"
    ).name("client_user.kick")
    admin.get("/task_monitor/tasks", admin_task_monitor.list_tasks).permission(
        "admin:task_monitor:list"
    ).name("task_monitor.tasks")
    admin.post("/task_monitor/trigger", admin_task_monitor.trigger).permission(
        "admin:task_monitor:trigger"
    ).name("task_monitor.trigger")
    admin.get("/task_monitor/queue", admin_task_monitor.queue_status).permission(
        "admin:task_monitor:list"
    ).name("task_monitor.queue")


def _register_db_migration_monitor(admin) -> None:
    admin.post("/db_backup/backup", admin_db_backup.manual_backup).permission(
        "admin:db_backup:create"
    ).name("db_backup.backup")
    admin.get("/db_backup/download", admin_db_backup.download_backup).permission(
        "admin:db_backup:list"
    ).name("db_backup.download")
    admin.get("/migration/list", admin_migration.migration_list).permission(
        "admin:migration:list"
    ).name("migration.list")
    admin.post("/migration/run", admin_migration.migration_run).permission(
        "admin:migration:run"
    ).name("migration.run")
    admin.get("/monitor/metrics", admin_monitor.monitor_metrics).permission(
        "admin:monitor:list"
    ).name("monitor.metrics")


def _register_import_session_cache(admin) -> None:
    admin.get("/import/template", admin_import.import_template).name(
        "import.template"
    )
    admin.post("/import/upload", admin_import.import_upload).name("import.upload")
    admin.get("/session/list", admin_session.session_list).permission(
        "admin:session:list"
    ).name("session.list")
    admin.post("/session/kick", admin_session.session_kick).permission(
        "admin:session:kick"
    ).name("session.kick")
    admin.get("/cache/stats", admin_cache.cache_stats).permission(
        "admin:cache:stats"
    ).name("cache.stats")
    admin.post("/cache/clear", admin_cache.cache_clear).permission(
        "admin:cache:clear"
    ).name("cache.clear")


def _register_trash(admin) -> None:
    admin.get("/trash/modules", admin_trash.trash_modules).permission(
        "admin:trash:list"
    ).name("trash.modules")
    admin.get("/trash/list", admin_trash.trash_list).permission(
        "admin:trash:list"
    ).name("trash.list")
    admin.post("/trash/restore", admin_trash.trash_restore).permission(
        "admin:trash:restore"
    ).name("trash.restore")
    admin.post("/trash/purge", admin_trash.trash_purge).permission(
        "admin:trash:purge"
    ).name("trash.purge")


def _register_private_file(routes: RouteRegistry) -> None:
    private_file = routes.group(
        prefix="/api",
        name="private-file.",
        middleware=[require_admin],
        access=RouteAccess.ADMIN,
    )
    private_file.get("/file/{file_id}", admin_file.proxy_private_file).name("show")
