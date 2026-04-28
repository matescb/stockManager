from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import (
    attachments,
    auth,
    custom_fields,
    lots,
    parts,
    projects,
    search,
    stock,
    storage,
    tags,
    workspaces,
)
from app.core.config import settings
from app.core.responses import http_exception_handler, validation_exception_handler

app = FastAPI(title="Parts Inventory & Production Manager", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings().cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_exception_handler(HTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)

os.makedirs(settings().UPLOAD_DIR, exist_ok=True)

app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(workspaces.router, prefix="/api/workspaces", tags=["workspaces"])
app.include_router(parts.router, prefix="/api/parts", tags=["parts"])
app.include_router(storage.router, prefix="/api/storage", tags=["storage"])
app.include_router(stock.router, prefix="/api/stock", tags=["stock"])
app.include_router(lots.router, prefix="/api/lots", tags=["lots"])
app.include_router(projects.router, prefix="/api/projects", tags=["projects"])
app.include_router(attachments.router, prefix="/api/attachments", tags=["attachments"])
app.include_router(custom_fields.router, prefix="/api/custom-fields", tags=["custom_fields"])
app.include_router(tags.router, prefix="/api/tags", tags=["tags"])
app.include_router(search.router, prefix="/api/search", tags=["search"])


@app.get("/api/health")
def health():
    return {"data": {"status": "ok"}, "status": {"category": "ok", "message": "OK"}}
