"""Pages of the inspection station: /station, /station/history, /station/inspection/{id}."""

from datetime import datetime
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app import config, repository

router = APIRouter()
templates = Jinja2Templates(directory=config.TEMPLATE_DIR)

CHANGE_NAMES = {"REJECTED": "falsch erkannt", "TYPE_CHANGED": "anderer Typ", "MISSED": "Fehler übersehen"}
DECISION_NAMES = {"CONFIRMED": "Bestätigt", "CORRECTED": "Korrigiert"}


def fmt_datetime(value: str | None) -> str:
    return datetime.fromisoformat(value).strftime("%d.%m.%Y %H:%M") if value else "–"


def fmt_number(value: float, digits: int = 1) -> str:
    return f"{value:.{digits}f}".replace(".", ",")


templates.env.filters["datetime_de"] = fmt_datetime
templates.env.filters["number_de"] = fmt_number


def base_context() -> dict:
    return {"app_title": config.APP_TITLE, "station_name": config.STATION_NAME}


def station_config() -> dict:
    """Values the browser script needs; no rules are evaluated in the browser."""
    return {
        "interval_ms": int(config.STATION_INFEED_INTERVAL_S * 1000),
        "confidence_min": config.CONFIDENCE_MIN,
        "image_width": config.IMAGE_WIDTH_PX,
        "image_height": config.IMAGE_HEIGHT_PX,
        "defect_types": config.DEFECT_TYPES,
        "non_defect_types": sorted(config.NON_DEFECT_TYPES),
        "verdicts": config.VERDICTS,
        "comment_max_length": config.STATION_COMMENT_MAX_LENGTH,
    }


@router.get("/station", response_class=HTMLResponse)
def station(request: Request):
    interval = f"{config.STATION_INFEED_INTERVAL_S:g}".replace(".", ",")
    return templates.TemplateResponse(
        request,
        "station/index.html",
        {**base_context(), "station_config": station_config(), "interval_label": interval},
    )


@router.get("/station/history", response_class=HTMLResponse)
def history(
    request: Request,
    scope: str = "flagged",
    status: str = "",
    final: str = "",
    corrected: str = "",
    page: int = 1,
):
    scope = scope if scope in repository.HISTORY_SCOPES else "flagged"
    filters = {"scope": scope, "status": status, "final": final, "corrected": corrected}
    with repository.connection() as conn:
        result = repository.history(conn, scope, status or None, final or None, corrected == "1", page)

    def page_url(number: int) -> str:
        params = {k: v for k, v in filters.items() if v}
        params["page"] = number
        return f"/station/history?{urlencode(params)}"

    return templates.TemplateResponse(
        request,
        "station/history.html",
        {
            **base_context(),
            **result,
            "filters": filters,
            "scopes": repository.HISTORY_SCOPES,
            "statuses": repository.HISTORY_STATUSES,
            "finals": repository.HISTORY_FINALS,
            "verdicts": config.VERDICTS,
            "page_url": page_url,
            "back_query": urlencode({**{k: v for k, v in filters.items() if v}, "page": result["page"]}),
        },
    )


@router.get("/station/inspection/{inspection_id}", response_class=HTMLResponse)
def inspection(request: Request, inspection_id: int, back: str = ""):
    with repository.connection() as conn:
        detail = repository.inspection_detail(conn, inspection_id)
    numbers = {d["id"]: index + 1 for index, d in enumerate(detail["detections"])}
    types = {d["id"]: d["type_name"] for d in detail["detections"]}
    changes = []
    if detail["review"]:
        for c in detail["review"]["changes"]:
            target = (
                f"{numbers[c['detection_id']]} {types[c['detection_id']]}" if c["detection_id"] in numbers else "–"
            )
            new_type = config.DEFECT_TYPES.get(c["new_defect_type"], "") if c["new_defect_type"] else ""
            changes.append({"what": CHANGE_NAMES[c["change"]], "target": target, "new_type": new_type})
    return templates.TemplateResponse(
        request,
        "station/detail.html",
        {
            **base_context(),
            "d": detail,
            "changes": changes,
            "verdicts": config.VERDICTS,
            "statuses": repository.HISTORY_STATUSES,
            "decisions": DECISION_NAMES,
            "image_width": config.IMAGE_WIDTH_PX,
            "image_height": config.IMAGE_HEIGHT_PX,
            "confidence_min": config.CONFIDENCE_MIN,
            "back_url": "/station/history" + (f"?{back}" if back else ""),
        },
    )
