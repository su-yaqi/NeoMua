from fastapi import APIRouter

from app.api.routes import (
    agent_tasks,
    items,
    llm_provider_configs,
    login,
    namespaces,
    node_enrollment,
    node_socket,
    private,
    runtime_artifacts,
    runtime_internal,
    runtimes,
    users,
    utils,
)
from app.core.config import settings

api_router = APIRouter()
api_router.include_router(agent_tasks.router)
api_router.include_router(login.router)
api_router.include_router(users.router)
api_router.include_router(utils.router)
api_router.include_router(items.router)
api_router.include_router(namespaces.router)
api_router.include_router(namespaces.platform_router)
api_router.include_router(llm_provider_configs.router)
api_router.include_router(runtimes.router)
api_router.include_router(runtime_internal.router)
api_router.include_router(runtime_artifacts.router)
api_router.include_router(runtime_artifacts.node_router)
api_router.include_router(node_enrollment.admin_router)
api_router.include_router(node_enrollment.node_router)
api_router.include_router(node_socket.router)


if settings.ENVIRONMENT == "local":
    api_router.include_router(private.router)
