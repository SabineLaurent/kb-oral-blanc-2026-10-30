"""GET /health of both APIs, the probe of a container, served without key: it says the process
answers, nothing more.

A probe that opened the base would restart the container in a loop while the base is missing."""

from starlette.requests import Request
from starlette.responses import JSONResponse

HEALTH_PATH = "/health"


async def answer_health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})
