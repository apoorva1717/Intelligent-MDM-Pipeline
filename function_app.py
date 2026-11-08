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


@azure_app.timer_trigger(schedule="0 */1 * * * *", arg_name="timer", run_on_startup=True)
async def vat_jobs_timer(timer: func.TimerRequest) -> None:
    """Every minute: continue a VAT job that waits, or whose worker is gone
    (a restart, a recycle, a deployment), from its next unfinished chunk
    (vat/jobs.py). Returns at once; the job runs in the background."""
    from api.vat_routes import resume_vat_jobs

    resume_vat_jobs()
