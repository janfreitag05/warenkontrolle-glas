"""FastAPI entry point. Start with: uvicorn app.main:app"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import config, db
from app.repository import StationError
from app.routes import api, dashboard, station


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = db.connect()
    try:
        db.init_db(conn)
    finally:
        conn.close()
    yield


app = FastAPI(title=config.APP_TITLE, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")
templates = Jinja2Templates(directory=config.TEMPLATE_DIR)
app.include_router(station.router)
app.include_router(api.router)
app.include_router(dashboard.router)
app.add_exception_handler(StationError, api.station_error_handler)


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {"app_title": config.APP_TITLE, "station_name": config.STATION_NAME},
    )
