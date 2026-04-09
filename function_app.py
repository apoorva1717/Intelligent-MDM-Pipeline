"""Azure Function v2 ASGI entry point.

Wraps the shared FastAPI app for deployment as an Azure Function.
The same routes, middleware, and logic apply — no duplication.
"""

import azure.functions as func
from azure.functions import AsgiFunctionApp

from api.app import app

azure_app = AsgiFunctionApp(app=app, http_auth_level=func.AuthLevel.FUNCTION)
