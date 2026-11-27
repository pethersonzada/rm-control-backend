import hashlib
import re
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager

from fastapi import HTTPException, Request
from starlette.responses import JSONResponse

from app.config import Identity, store_key
from app.firebase_auth import FirebaseUnavailable, InvalidFirebaseToken, membership, verify_uid
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool


class RateLimiter:
    def __init__(self):
        self.windows = OrderedDict()
        self.lock = threading.Lock()
        self.imports = threading.BoundedSemaphore(2)
        self.workbooks = threading.BoundedSemaphore(1)

    def check(self, key, limit):
        minute = int(time.monotonic() // 60)
        with self.lock:
            while self.windows and next(iter(self.windows.values()))[0] < minute:
                self.windows.popitem(last=False)
            if key not in self.windows and len(self.windows) >= 4096:
                raise HTTPException(429, "Limite de requisições atingido.", headers={"Retry-After": "60"})
            _, count = self.windows.get(key, (minute, 0))
            if count >= limit:
                raise HTTPException(429, "Limite de requisições atingido.", headers={"Retry-After": "60"})
            self.windows[key] = (minute, count + 1)

    @contextmanager
    def import_slot(self, workbook=False):
        semaphore = self.workbooks if workbook else self.imports
        if not semaphore.acquire(blocking=False):
            raise HTTPException(429, "Importações em processamento. Tente novamente.", headers={"Retry-After": "5"})
        try:
            yield
        finally:
            semaphore.release()


def authenticate(request: Request) -> Identity:
    settings = request.app.state.settings
    if not settings.enabled:
        raise HTTPException(503, "Ambiente operacional indisponível.")
    limiter = request.app.state.limiter
    address = request.client.host if request.client else "unknown"
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    principal = None
    if scheme.lower() == "bearer" and settings.firebase_project_id and token.count(".") == 2:
        limiter.check(("firebase", address), 300)
        try:
            uid = verify_uid(token, settings.firebase_project_id)
            principal = membership(request.app.state.engine, settings.firebase_project_id, uid)
        except InvalidFirebaseToken:
            raise HTTPException(401, "Credencial Firebase inválida.", headers={"WWW-Authenticate": "Bearer"}) from None
        except (FirebaseUnavailable, SQLAlchemyError):
            raise HTTPException(503, "Autenticação temporariamente indisponível.") from None
        if principal is None and request.method == "POST" and request.url.path == "/access/request":
            return Identity("pending", "", "viewer", frozenset(), settings.firebase_project_id, uid)
        if principal is None:
            raise HTTPException(403, "Conta sem autorização para acessar o RM Control.",
                                headers={"X-RM-Account-Denied": "1"})
    elif scheme.lower() == "bearer" and (not settings.firebase_project_id or settings.allow_legacy_token) and re.fullmatch(r"[!-~]{32,128}", token):
        principal = settings.identities.get(hashlib.sha256(token.encode("ascii")).hexdigest())
    if principal is None:
        limiter.check(("anonymous", address), 60)
        raise HTTPException(401, "Credencial individual obrigatória ou inválida.", headers={"WWW-Authenticate": "Bearer"})
    limiter.check((principal.organization, principal.subject), 180)
    return principal


def can_write(principal: Identity) -> Identity:
    if principal.role not in {"importer", "admin"}:
        raise HTTPException(403, "Perfil sem permissão para gravar.")
    return principal


def require_store(principal: Identity, destination: str):
    if store_key(destination) not in principal.stores:
        raise HTTPException(403, "Loja fora do seu escopo.")


class RequestLimits:
    limits = {
        "/imports/preview": 4_100_000,
        "/imports/spreadsheet": 28_010_000,
        "/imports/workbook": 21_010_000,
        "/imports/confirm": 12_000_000,
        "/inventory/reconcile": 2_000_000,
        "/tickets": 2_000_000,
    }

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def secure_send(message):
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                headers.extend([(b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff")])
            await send(message)

        request = Request(scope)
        path = request.url.path
        if request.method == "OPTIONS" or path in {"/health", "/ready"}:
            await self.app(scope, receive, secure_send)
            return
        try:
            principal = await run_in_threadpool(authenticate, request)
            if path.startswith("/admin/") and (principal.role != "admin" or not principal.firebase_uid):
                raise HTTPException(403, "Acesso administrativo restrito.")
            if path.startswith("/imports/") or (path.startswith("/tickets") and request.method in {"POST", "PUT"}) or request.method == "PATCH":
                can_write(principal)
            request.state.principal = principal
            limit = self.limits.get(path, 2_000_000 if path.startswith('/tickets/') and request.method == 'PUT' else 16384)
            if request.app.state.settings.mode == "operational" and path == "/imports/spreadsheet":
                limit = 1_000_000
            if int(request.headers.get("content-length", "0")) > limit:
                raise HTTPException(413, "Tamanho máximo da requisição excedido.")
        except ValueError:
            await JSONResponse({"detail": "Comprimento inválido."}, 400)(scope, receive, secure_send)
            return
        except HTTPException as error:
            await JSONResponse({"detail": error.detail}, error.status_code, headers=error.headers)(scope, receive, secure_send)
            return

        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            received += len(message.get("body", b""))
            if received > limit:
                raise HTTPException(413, "Tamanho máximo da requisição excedido.")
            return message

        if request.method == "POST" and path in {"/imports/workbook", "/imports/spreadsheet"}:
            try:
                with request.app.state.limiter.import_slot(workbook=True):
                    await self.app(scope, limited_receive, secure_send)
            except HTTPException as error:
                await JSONResponse({"detail": error.detail}, error.status_code, headers=error.headers)(
                    scope, receive, secure_send,
                )
        else:
            await self.app(scope, limited_receive, secure_send)


def current_identity(request: Request) -> Identity:
    return request.state.principal


def writer(request: Request) -> Identity:
    return can_write(current_identity(request))
