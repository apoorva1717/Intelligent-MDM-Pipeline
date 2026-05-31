"""Azure Function v2 ASGI entry point.

Wraps the shared FastAPI app for deployment as an Azure Function.
The same routes, middleware, and logic apply — no duplication.
"""

import azure.functions as func
from azure.functions import AsgiMiddleware

from api.app import app as fastapi_app

azure_app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)


@azure_app.route(route="{*route}")
async def http_app_func(
    req: func.HttpRequest, context: func.Context
) -> func.HttpResponse:
    return await AsgiMiddleware(fastapi_app).handle_async(req, context)
