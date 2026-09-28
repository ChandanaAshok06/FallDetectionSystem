from fastapi import FastAPI
from app.api.predict import router as predict_router
from app.api.predictions import router as predictions_router
from app.api.alerts import router as alerts_router
from app.db.session import engine, Base
from app.db import models 
from app.api.auth import router as auth_router
from app.api.devices import router as devices_router

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Fall Detection API")

@app.get("/health")
def health_check():
    return {"status": "ok"}

app.include_router(predict_router)
app.include_router(predictions_router) 
app.include_router(alerts_router)
app.include_router(auth_router)
app.include_router(devices_router)