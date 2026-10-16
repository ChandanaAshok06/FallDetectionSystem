import asyncio
from contextlib import asynccontextmanager
from app.core.escalation import escalation_loop
from fastapi import FastAPI
from app.api.predict import router as predict_router
from app.api.predictions import router as predictions_router
from app.api.alerts import router as alerts_router
from app.db.session import engine, Base
from app.db import models 
from app.api.auth import router as auth_router
from app.api.devices import router as devices_router

from app.core import notify  # registers notifications table
from app.continual import store as continual_store  # registers cl_samples / cl_models
from app.api.continual import router as continual_router
import os
from fastapi.middleware.cors import CORSMiddleware
from app.api.push import router as push_router
Base.metadata.create_all(bind=engine)

@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(escalation_loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

app = FastAPI(title="Fall Detection API", lifespan=lifespan)
origins = [o.strip() for o in os.getenv(
    "CORS_ORIGINS",
    "http://localhost:5173,http://localhost:5174,http://127.0.0.1:5173,http://127.0.0.1:5174"
).split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health_check():
    return {"status": "ok"}

app.include_router(predict_router)
app.include_router(predictions_router) 
app.include_router(alerts_router)
app.include_router(auth_router)
app.include_router(devices_router)
app.include_router(continual_router)
app.include_router(push_router)