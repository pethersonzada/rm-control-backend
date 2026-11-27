import logging
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import ArgumentError, SQLAlchemyError

from app.config import load_settings
from app.database import connect
from app.models import AccessRequest, FirebaseMembership, FinancialReview, NativeTicket, Occurrence, OperationalPolicy, Store, SupplierCheck, UnitReference
from app.routers import admin, catalog, imports, operations, policies, stores, supplier_checks, tickets, units
from app.security import RateLimiter, RequestLimits

logger = logging.getLogger("rm_control")


def create_app(settings=None, engine=None):
    settings = settings or load_settings()
    app = FastAPI(redirect_slashes=False, title="RM Control API", version="0.3.0", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    if engine is None and settings.enabled:
        try:
            engine = connect(settings.database_url)
        except (ArgumentError, ValueError):
            logger.error("database_configuration_invalid")
    app.state.engine = engine
    app.state.limiter = RateLimiter()
    app.add_middleware(RequestLimits)
    app.add_middleware(
        CORSMiddleware, allow_origins=list(settings.origins),
        allow_methods=["GET", "POST", "PATCH", "PUT"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
        expose_headers=["X-RM-Account-Denied", "Retry-After"],
    )

    @app.middleware("http")
    async def observe(request: Request, call_next):
        started = time.monotonic()
        response = await call_next(request)
        route = request.scope.get("route")
        logger.info(
            "http_request route=%s method=%s status=%d duration_ms=%.1f",
            getattr(route, "path", "unmatched"), request.method, response.status_code,
            (time.monotonic() - started) * 1000,
        )
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_input(_request, error):
        fields = [{"field": ".".join(str(part) for part in item["loc"]), "type": item["type"]}
                  for item in error.errors()[:20]]
        return JSONResponse({"detail": "Campos inválidos.", "fields": fields}, 422)

    @app.exception_handler(SQLAlchemyError)
    async def database_error(_request, _error):
        logger.warning("database_operation_failed")
        return JSONResponse({"detail": "Banco indisponível ou operação interrompida. Tente novamente."}, 503)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/ready")
    def ready():
        if not settings.enabled or engine is None:
            raise HTTPException(503, "Ambiente operacional indisponível.")
        with engine.connect() as connection:
            connection.execute(select(Occurrence.id).limit(1))
            connection.execute(select(NativeTicket.occurrence_id).limit(1))
            connection.execute(select(SupplierCheck.id).limit(1))
            connection.execute(select(OperationalPolicy.organization).limit(1))
            connection.execute(select(FinancialReview.occurrence_id).limit(1))
            connection.execute(select(UnitReference.id).limit(1))
            if settings.firebase_project_id:
                connection.execute(select(Store.id).limit(1))
                connection.execute(select(AccessRequest.id).limit(1))
                connection.execute(select(FirebaseMembership.id).limit(1))
        return {"status": "ready"}

    app.include_router(units.router)
    app.include_router(policies.router)
    app.include_router(supplier_checks.router)
    app.include_router(catalog.router)
    app.include_router(stores.router)
    app.include_router(tickets.router)
    app.include_router(admin.router)
    app.include_router(operations.router)
    app.include_router(imports.router)
    return app


app = create_app()
