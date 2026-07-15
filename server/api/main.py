"""
FastAPI 应用入口
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from api.routers import (
    auth,
    projects,
    xhs,
    douyin,
    config,
    browser,
    project_api,
    agent,
    mobile,
    bootstrap,
    observability,
    downloads,
    skills,
    prompts,
    voice,
    storage,
    aigc,
    mobile_collect,
    persons,
    artifacts,
    context,
    scholar_contact,
    dingtalk,
)
from api.config import get_settings
from api.auth import get_current_active_user, User
from core.logger import get_logger

import socketio
from AutoGLM_GUI.socketio_server import sio as scrcpy_sio

settings = get_settings()
logger = get_logger("api.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 提高文件描述符限制（并发 Playwright + Docker 需要大量 fd）
    try:
        import resource
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        target = min(1048576, hard)
        if soft < target:
            resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
            logger.info(f"ulimit -n 已提高: {soft} → {target}")
    except Exception:
        pass

    # 启动时：初始化默认用户和配置
    from api.db.mongodb import get_db
    from api.dao import users as users_dao
    from api.dao import config as config_dao
    
    try:
        db = get_db()
        await users_dao.ensure_default_users(db)
        await users_dao.ensure_default_config(db)
        try:
            from core.mobile.easytier import (
                build_easytier_config_from_env,
                set_easytier_runtime_config,
            )

            easytier_doc = await config_dao.get_config(db, "easytier")
            if easytier_doc:
                easytier_config = easytier_doc.get("config", {})
            else:
                easytier_config = build_easytier_config_from_env()
                await config_dao.set_config(db, "easytier", easytier_config)
            set_easytier_runtime_config(easytier_config)
            logger.info("EasyTier 配置已从 MongoDB 注入")
        except Exception as e:
            logger.warning(f"EasyTier 配置注入失败（不影响运行）: {e}")
        try:
            counts = await config_dao.reencrypt_all_configs(db)
            if counts.get("updated"):
                logger.info(
                    "system_config 敏感字段已重写加密: "
                    f"updated={counts['updated']}, scanned={counts['scanned']}"
                )
        except Exception as e:
            logger.warning(f"system_config 加密迁移失败（不影响运行）: {e}")
        try:
            from browser_manager import configure_browser_provider

            chrome_doc = await config_dao.get_config(db, "chrome_docker")
            configure_browser_provider(chrome_doc.get("config", {}) if chrome_doc else {})
            logger.info("Chrome Docker 配置已从 MongoDB 注入")
        except Exception as e:
            logger.warning(f"Chrome Docker 配置注入失败（不影响运行）: {e}")
        logger.info("用户和配置已从 MongoDB 加载")
    except Exception as e:
        logger.warning(f"初始化用户/配置失败: {e}")

    # 启动时：初始化 TokenTracker（内存环形缓冲 + MongoDB 批量落库/回填）
    try:
        from Sere1nGraph.graph.observability import get_global_tracker

        tracker = get_global_tracker()
        tracker.set_db(get_db())
        await tracker.flush_pending()
        await tracker.load_history_from_db()
        tracker.start_flusher()
        logger.info("TokenTracker 已初始化，历史数据已加载，落库 flusher 已启动")
    except Exception as e:
        logger.warning(f"TokenTracker 初始化失败（不影响运行）: {e}")

    # 启动时：初始化观测日志收集器（进程内环形缓冲，不读写 MongoDB）
    try:
        from core.observability import get_obs_logger

        get_obs_logger()
        logger.info("观测日志收集器已启动（内存环形缓冲）")
    except Exception as e:
        logger.warning(f"观测日志收集器初始化失败（不影响运行）: {e}")

    # 启动时：确保核心集合索引存在（幂等）
    try:
        db = get_db()
        # findings — 核心数据元
        try:
            await db["findings"].create_index("finding_id", sparse=True)
        except Exception:
            pass  # 已有同名索引（可能是 unique 的），跳过
        await db["findings"].create_index("project_id")
        await db["findings"].create_index([("project_id", 1), ("source", 1)])
        await db["findings"].create_index([("project_id", 1), ("attention_score", -1)])
        await db["findings"].create_index("xhs_user_id", sparse=True)
        await db["findings"].create_index("note_id", sparse=True)
        await db["findings"].create_index("task_id")
        # copywritings
        try:
            await db["copywritings"].create_index("finding_id")
        except Exception:
            pass
        await db["copywritings"].create_index("project_id")
        await db["copywritings"].create_index("task_id")
        # profiles
        try:
            await db["profiles"].create_index("finding_id", sparse=True)
        except Exception:
            pass
        await db["profiles"].create_index("project_id")
        # xhs 集合
        await db["xhs_notes"].create_index("note_id")
        await db["xhs_notes"].create_index([("project_id", 1), ("task_id", 1)])
        await db["xhs_notes"].create_index([("task_id", 1), ("tagging.is_suspicious", 1)])
        await db["xhs_note_details"].create_index("note_id")
        await db["xhs_note_details"].create_index("project_id")
        await db["xhs_profiles"].create_index([("project_id", 1), ("user_id", 1)])
        from api.services.xhs_runtime import ensure_indexes as ensure_xhs_runtime_indexes
        await ensure_xhs_runtime_indexes(db)
        # tasks
        await db["tasks"].create_index("task_id")
        await db["tasks"].create_index([("project_id", 1), ("task_type", 1)])
        # contact_profiles — 系统3 人物画像
        from api.dao import contact_profiles as contact_profiles_dao
        await contact_profiles_dao.ensure_indexes(db)
        # 手机 AI 相关集合(系统1/4/5)
        from api.dao import chat_suggestions as chat_suggestions_dao
        from api.dao import auto_chat_sessions as auto_chat_sessions_dao
        from api.dao import mobile_artifacts as mobile_artifacts_dao
        from api.dao import mobile_profile_observations as mobile_profile_observations_dao
        from api.dao import device_reservations as device_reservations_dao
        from api.dao import device_metadata as device_metadata_dao
        await chat_suggestions_dao.ensure_indexes(db)
        await auto_chat_sessions_dao.ensure_indexes(db)
        await mobile_artifacts_dao.ensure_indexes(db)
        await mobile_profile_observations_dao.ensure_indexes(db)
        await device_reservations_dao.ensure_indexes(db)
        await device_metadata_dao.ensure_indexes(db)
        # Skills / Prompts 技能库与提示词库索引
        from api.dao import skills as skills_dao
        from api.dao import prompts as prompts_dao
        await skills_dao.ensure_indexes(db)
        await prompts_dao.ensure_indexes(db)
        from api.services.library_runtime import refresh_ai_libraries
        library_counts = await refresh_ai_libraries(db, seed_if_empty=True)
        logger.info(
            "AI 技能/提示词运行时已从 MongoDB 加载: "
            f"skills={library_counts['skills_loaded']}, "
            f"prompts={library_counts['prompts_loaded']}"
        )
        # Voice 声音复刻索引
        from api.dao import voice as voice_dao
        await voice_dao.ensure_indexes(db)
        # FOFA 资产 / 公司元信息索引
        from api.dao import fofa_assets as fofa_assets_dao
        from api.dao import company_meta as company_meta_dao
        await fofa_assets_dao.ensure_indexes(db)
        await company_meta_dao.ensure_indexes(db)
        # 学者学术联系发现索引
        from api.dao import scholar_contact as scholar_contact_dao
        await scholar_contact_dao.ensure_indexes(db)
        # 人设库 — 统一人物实体索引
        from api.dao import persons as persons_dao
        await persons_dao.ensure_indexes(db)
        # AI 中枢对话留存索引
        from api.dao import ai_hub as ai_hub_dao
        await ai_hub_dao.ensure_indexes(db)
        # 产物（Word 文档等）索引
        from api.dao import artifacts as artifacts_dao
        await artifacts_dao.ensure_indexes(db)
        from api.dao import storage_objects as storage_objects_dao
        from api.dao import storage_migrations as storage_migrations_dao
        await storage_objects_dao.ensure_indexes(db)
        await storage_migrations_dao.ensure_indexes(db)
        # 手机采集任务框架 — 任务定义/增量记录/调度索引
        from api.dao import mobile_collect as mobile_collect_dao
        from api.dao import schedules as schedules_dao
        await mobile_collect_dao.ensure_indexes(db)
        await schedules_dao.ensure_indexes(db)
        logger.info("核心集合索引已确认")
    except Exception as e:
        logger.warning(f"索引创建失败（不影响运行）: {e}")
    
    # 启动时:恢复设备独占预约到内存资源池(系统1 持久化)
    try:
        db = get_db()
        from api.dao import device_reservations as device_reservations_dao

        items = await device_reservations_dao.list_reservations(db)
        if items:
            from core.mobile.pool import DevicePool

            n = DevicePool.get_instance().load_reservations(items)
            logger.info(f"已恢复 {n} 条设备独占预约")
    except Exception as e:
        logger.warning(f"恢复设备预约失败(不影响运行): {e}")

    # 启动时:开启手机保活后台循环(低负载 ADB 易断,须常驻保活)
    try:
        from core.mobile.keepalive import MobileKeepAlive

        MobileKeepAlive.get_instance().start()
        logger.info("手机保活循环已启动")
    except Exception as e:
        logger.warning(f"手机保活启动失败(不影响运行): {e}")

    # 启动时:开启定时调度器(手机采集任务等定时触发)
    try:
        from api.services.scheduler import TaskScheduler

        TaskScheduler.get_instance().start()
        logger.info("定时调度器已启动")
    except Exception as e:
        logger.warning(f"定时调度器启动失败(不影响运行): {e}")

    # 启动时：按数据库配置建立钉钉 Stream Mode 长连接
    try:
        from api.services.dingtalk_stream import DingTalkStreamManager

        await DingTalkStreamManager.get_instance().reload_all()
        logger.info("钉钉 Stream Mode 配置已加载")
    except Exception as e:
        logger.warning(f"钉钉 Stream Mode 启动失败(不影响运行): {e}")

    yield

    # 关闭时：先断开钉钉长连接，停止接收新对话
    try:
        from api.services.dingtalk_stream import DingTalkStreamManager

        await DingTalkStreamManager.get_instance().stop()
        logger.info("钉钉 Stream Mode 已停止")
    except Exception as e:
        logger.warning(f"钉钉 Stream Mode 停止失败: {e}")

    # 关闭时:停止手机保活后台循环
    try:
        from core.mobile.keepalive import MobileKeepAlive

        await MobileKeepAlive.get_instance().stop()
        logger.info("手机保活循环已停止")
    except Exception as e:
        logger.warning(f"手机保活停止失败: {e}")

    # 关闭时:停止定时调度器
    try:
        from api.services.scheduler import TaskScheduler

        await TaskScheduler.get_instance().stop()
        logger.info("定时调度器已停止")
    except Exception as e:
        logger.warning(f"定时调度器停止失败: {e}")

    # 关闭时：先 drain 观测组件，把内存待写记录全部落库（须先于 close_mongo）
    try:
        from Sere1nGraph.graph.observability import get_global_tracker
        await get_global_tracker().drain()
        logger.info("TokenTracker 待写记录已落库")
    except Exception as e:
        logger.warning(f"TokenTracker drain 失败: {e}")
    try:
        from core.observability import get_obs_logger
        await get_obs_logger().drain()
        logger.info("观测日志收集器已停止")
    except Exception as e:
        logger.warning(f"观测日志 drain 失败: {e}")

    # 关闭时：清理 Docker Chrome 容器
    try:
        from browser_manager import shutdown_provider
        await shutdown_provider()
        logger.info("Chrome 容器已清理")
    except Exception as e:
        logger.warning(f"Chrome 容器清理失败: {e}")

    # 关闭时：关闭 MongoDB 连接，释放连接池
    try:
        from api.db.mongodb import close_mongo
        close_mongo()
        logger.info("MongoDB 连接已关闭")
    except Exception as e:
        logger.warning(f"MongoDB 连接关闭失败: {e}")


app = FastAPI(
    title="AI Agent API",
    description="AI Agent 服务（仅限本地访问）",
    version="1.0.0",
    lifespan=lifespan,
)

# 配置 CORS - 允许所有本地请求
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",  # 匹配所有本地端口
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============ 全局异常处理（统一错误响应结构，流式安全） ============
# 仅在响应开始前生效，不影响已经开始的 SSE/StreamingResponse。

@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "path": request.url.path},
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"detail": jsonable_encoder(exc.errors()), "path": request.url.path},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception(f"未处理异常 {request.method} {request.url.path}: {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": "服务器内部错误", "path": request.url.path},
    )


# 注册路由
app.include_router(auth.router, prefix="/api/v1/auth", tags=["认证"])
app.include_router(projects.router, prefix="/api/v1/projects", tags=["项目"])
app.include_router(xhs.router, prefix="/api/v1/xhs", tags=["小红书"])
app.include_router(douyin.router, prefix="/api/v1/douyin", tags=["抖音"])
app.include_router(config.router, prefix="/api/v1/config", tags=["配置管理"])
app.include_router(browser.router, prefix="/api/v1", tags=["浏览器管理"])
app.include_router(skills.router, prefix="/api/v1/skills", tags=["技能库"])
app.include_router(prompts.router, prefix="/api/v1/prompts", tags=["提示词库"])
app.include_router(project_api.router, prefix="/api/v1", tags=["项目统一API"])
app.include_router(scholar_contact.router, prefix="/api/v1", tags=["学者学术联系"])
app.include_router(agent.router, prefix="/api/v1/agent", tags=["Agent"])
app.include_router(mobile.router, prefix="/api/v1/mobile", tags=["手机"])
app.include_router(mobile_collect.router, prefix="/api/v1/mobile-collect", tags=["手机采集任务"])
app.include_router(persons.router, prefix="/api/v1/persons", tags=["人设库"])
app.include_router(artifacts.router, prefix="/api/v1/artifacts", tags=["产物"])
app.include_router(storage.router, prefix="/api/v1/storage", tags=["对象存储"])
app.include_router(context.router, prefix="/api/v1/context", tags=["上下文聚合"])
app.include_router(bootstrap.router, prefix="/api/v1/bootstrap", tags=["Bootstrap"])
app.include_router(observability.router, prefix="/api/v1/observability", tags=["观测层"])
app.include_router(downloads.router, prefix="/api/v1/downloads", tags=["下载"])
app.include_router(voice.router, prefix="/api/v1/voice", tags=["声音复刻"])
app.include_router(aigc.router, prefix="/api/v1/aigc", tags=["AIGC"])
app.include_router(dingtalk.router, prefix="/api/v1/dingtalk", tags=["钉钉机器人"])


@app.get("/")
async def root(current_user: User = Depends(get_current_active_user)):
    return {"message": "AI Agent API", "docs": "/docs", "user": current_user.username}


@app.get("/health")
async def health():
    """健康检查：附带 MongoDB 连通性探测（带超时，不阻塞）。"""
    from api.db.mongodb import health_check

    db_status = await health_check()
    return {
        "status": "ok" if db_status.get("ok") else "degraded",
        "mongodb": db_status,
    }


# ============ Socket.IO 视频流挂载 ============
# 用 ASGIApp 包裹:非 /socket.io 请求转发给 FastAPI(app),
# /socket.io 交给 AutoGLM 的 scrcpy 视频流服务。run.py 启动 socket_app。
socket_app = socketio.ASGIApp(
    scrcpy_sio,
    other_asgi_app=app,
    socketio_path="/socket.io",
)
