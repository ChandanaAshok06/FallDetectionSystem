import math
from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


def _sanitize(obj):
    """Replace NaN/Infinity (not valid JSON) with their string form so the 422 can be serialized."""
    if isinstance(obj, float) and not math.isfinite(obj):
        return repr(obj)
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    return obj


async def validation_exception_handler(request: Request, exc: RequestValidationError):
    # FastAPI's default handler echoes the rejected input; a NaN/Infinity input then fails
    # JSON serialization and the client gets a 500 instead of a 422.
    return JSONResponse(
        status_code=422,
        content={"detail": jsonable_encoder(_sanitize(exc.errors()))},
    )
