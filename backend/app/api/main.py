from fastapi import APIRouter

from app.agent_management.capability_routes import (
    mcp_internal_router,
)
from app.agent_management.capability_routes import (
    node_router as agent_capability_node_router,
)
from app.agent_management.capability_routes import router as agent_capability_router
from app.agent_management.cli_routes import router as operator_cli_router
from app.agent_management.release_routes import (
    internal_router as agent_release_internal_router,
)
from app.agent_management.release_routes import router as agent_release_router
from app.agent_management.routes import router as agent_management_router
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
from app.conversation_management.routes import router as conversation_router
from app.core.config import settings
from app.project_management.routes import router as project_router
from app.workflow_management.routes import internal_router as workflow_internal_router
from app.workflow_management.routes import router as workflow_router

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

api_router.include_router(agent_management_router)
api_router.include_router(agent_capability_router)
api_router.include_router(agent_capability_node_router)
api_router.include_router(mcp_internal_router)
api_router.include_router(agent_release_router)
api_router.include_router(agent_release_internal_router)
api_router.include_router(operator_cli_router)
api_router.include_router(project_router)
api_router.include_router(conversation_router)
api_router.include_router(workflow_router)
api_router.include_router(workflow_internal_router)


if settings.ENVIRONMENT == "local" and settings.ENABLE_PRIVATE_TEST_API:
    api_router.include_router(private.router)
