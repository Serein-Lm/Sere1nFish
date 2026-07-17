from __future__ import annotations

PROJECTS_COLLECTION = "projects"
WEB_TAGS_COLLECTION = "web_tagging_results"

# ── 统一 Findings 集合 ──
# 所有数据源（web_tagging、xhs、douyin）的 findings 统一存储
FINDINGS_COLLECTION = "findings"
COPYWRITINGS_COLLECTION = "copywritings"
PROFILES_COLLECTION = "profiles"

# XHS 小红书相关集合
XHS_COOKIES_COLLECTION = "xhs_cookies"
XHS_SEARCH_TASKS_COLLECTION = "xhs_search_tasks"
XHS_NOTES_COLLECTION = "xhs_notes"
XHS_NOTE_DETAILS_COLLECTION = "xhs_note_details"
XHS_PROFILES_COLLECTION = "xhs_profiles"

# XHS 主页截图分析集合
XHS_PROFILE_SCREENSHOTS_COLLECTION = "xhs_profile_screenshots"

# 抖音相关集合
DOUYIN_COOKIES_COLLECTION = "douyin_cookies"
DOUYIN_SEARCH_RESULTS_COLLECTION = "douyin_search_results"
DOUYIN_TAGGED_RESULTS_COLLECTION = "douyin_tagged_results"
DOUYIN_PROFILES_COLLECTION = "douyin_profiles"

# 观测层
TOKEN_USAGE_RECORDS_COLLECTION = "token_usage_records"

# 手机 Agent / 自动聊天
CONTACT_PROFILES_COLLECTION = "contact_profiles"
CHAT_SUGGESTIONS_COLLECTION = "chat_suggestions"
AUTO_CHAT_SESSIONS_COLLECTION = "auto_chat_sessions"
MOBILE_SCREENSHOTS_COLLECTION = "mobile_screenshots"
MOBILE_OPERATION_LOGS_COLLECTION = "mobile_operation_logs"
MOBILE_PROFILE_OBSERVATIONS_COLLECTION = "mobile_profile_observations"
MOBILE_TRANSFERS_COLLECTION = "mobile_transfers"

# URL 扫描（旧集合，保留兼容）
URL_SCAN_TASKS_COLLECTION = "url_scan_tasks"
URL_SCAN_RESULTS_COLLECTION = "url_scan_results"
URL_SCAN_FINDINGS_COLLECTION = "url_scan_findings"
URL_SCAN_COPYWRITINGS_COLLECTION = "url_scan_copywritings"

# 系统管理
USERS_COLLECTION = "system_users"
SYSTEM_CONFIG_COLLECTION = "system_config"

# Skills 技能库
SKILLS_COLLECTION = "skills"
SKILL_CATEGORIES_COLLECTION = "skill_categories"
SKILL_TAGS_COLLECTION = "skill_tags"

# Prompts 提示词库
PROMPTS_COLLECTION = "prompts"
PROMPT_CATEGORIES_COLLECTION = "prompt_categories"
PROMPT_TAGS_COLLECTION = "prompt_tags"

# 统一任务
TASKS_COLLECTION = "tasks"

# 手机采集任务框架 — 自定义任务定义 / 增量结果 / 定时调度
MOBILE_COLLECT_TASKS_COLLECTION = "mobile_collect_tasks"
MOBILE_COLLECT_RECORDS_COLLECTION = "mobile_collect_records"
TASK_SCHEDULES_COLLECTION = "task_schedules"

# 综合公司扫描
COMPANY_SCAN_COLLECTION = "company_scan_results"
PROFILE_COPYWRITINGS_COLLECTION = "profile_copywritings"

# 公司元信息（规范化全称 + 根域名 + 别名）
COMPANY_META_COLLECTION = "company_meta"

# 全局目标实体与项目关联。Target 跨项目聚类，ProjectTarget 保存项目搜索意图。
TARGETS_COLLECTION = "targets"
PROJECT_TARGETS_COLLECTION = "project_targets"

# 永久来源文档。文档按规范 URL 去重，版本按稳定内容哈希不可变保存；
# link 记录文档被哪个项目/Target/任务/关键词发现。
SOURCE_DOCUMENTS_COLLECTION = "source_documents"
SOURCE_DOCUMENT_VERSIONS_COLLECTION = "source_document_versions"
SOURCE_DOCUMENT_LINKS_COLLECTION = "source_document_links"

# FOFA 资产情报（按稳定 asset_id 增量入库）
FOFA_ASSETS_COLLECTION = "fofa_assets"

# 学者学术联系发现（按单位+方向收集，文章绑定的公开通讯邮箱）
# 任务复用统一 tasks 集合，不新建任务集合。
SCHOLAR_ARTICLES_COLLECTION = "scholar_articles"
SCHOLAR_CONTACTS_COLLECTION = "scholar_contacts"

# 招投标公告。记录按供应商稳定 ID 全局去重，通过 project_ids/target_ids 复用。
BIDDING_RECORDS_COLLECTION = "bidding_records"

# 人设库 — 统一人物实体（跨平台/跨项目真源，稳定 person_id）
PERSONS_COLLECTION = "persons"

# AI 中枢对话留存（会话 + 消息）
AI_HUB_CONVERSATIONS_COLLECTION = "ai_hub_conversations"
AI_HUB_MESSAGES_COLLECTION = "ai_hub_messages"

# 产物（Word 文档 / 招股书 / 人物背景报告 / 话术包）
ARTIFACTS_COLLECTION = "artifacts"

# 统一对象存储元数据。领域集合仅保存 storage_object_id，不感知 OSS/本地实现。
STORAGE_OBJECTS_COLLECTION = "storage_objects"
STORAGE_MIGRATIONS_COLLECTION = "storage_migrations"
