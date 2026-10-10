"""Dashboard for the managing director (/dashboard). All figures come from repository.py."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app import config, repository

router = APIRouter()
templates = Jinja2Templates(directory=config.TEMPLATE_DIR)


def fmt_int(value: int | None) -> str:
    return "–" if value is None else f"{value:,}".replace(",", ".")


def fmt_pct(value: float | None, digits: int = 1) -> str:
    return "–" if value is None else f"{value * 100:.{digits}f} %".replace(".", ",")


def fmt_date(value) -> str:
    return value.strftime("%d.%m.%Y")


templates.env.filters["int_de"] = fmt_int
templates.env.filters["pct_de"] = fmt_pct
templates.env.filters["date_de"] = fmt_date


def chart_data(data: dict) -> dict:
    """Plain data for the browser script; it only draws, it computes nothing."""
    trend = data["trend"]
    types = data["defect_types"]
    return {
        "days": [f"{d[8:10]}.{d[5:7]}." for d in trend["days"]],
        "total": trend["total"],      # {"rate": [...], "provisional": [...]}
        "lines": trend["lines"],      # {code: {"rate": [...], "provisional": [...]}}
        "line_names": {code: name for code, name in config.PRODUCTION_LINES},
        "defects": [{"label": config.DEFECT_TYPES[code], "count": n} for code, n in types["defects"]],
        "filtered": [{"label": config.DEFECT_TYPES[code], "count": n} for code, n in types["filtered"]],
    }


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(
    request: Request,
    period: str = config.DASHBOARD_DEFAULT_PERIOD,
    line: str = "",
    date_from: str = "",
    date_to: str = "",
    by_line: str = "",
):
    with repository.connection() as conn:
        data = repository.dashboard(conn, period, line or None, date_from or None, date_to or None, by_line == "1")
    return templates.TemplateResponse(
        request,
        "dashboard/index.html",
        {
            "app_title": config.APP_TITLE,
            **data,
            "periods": {key: label for key, (label, _) in config.DASHBOARD_PERIODS.items()},
            "lines": config.PRODUCTION_LINES,
            "line_name": dict(config.PRODUCTION_LINES).get(data["line"], "Alle Linien"),
            "chart": chart_data(data),
        },
    )
