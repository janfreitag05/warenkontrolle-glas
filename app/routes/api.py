"""JSON API for the inspection station. Responses never contain injected_error or gt_defect."""

from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from app import config, repository

router = APIRouter(prefix="/api/station", tags=["Prüfstation"])


class ChangeIn(BaseModel):
    change: Literal["REJECTED", "TYPE_CHANGED", "MISSED"]
    detection_id: int | None = None
    new_defect_type: str | None = None


class ReviewIn(BaseModel):
    decision: Literal["CONFIRMED", "CORRECTED"]
    final_verdict: Literal["OK", "NOK"] | None = None
    comment: str | None = Field(default=None, max_length=config.STATION_COMMENT_MAX_LENGTH)
    changes: list[ChangeIn] = []


async def station_error_handler(request: Request, exc: repository.StationError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})


@router.get("/state")
def state():
    with repository.connection() as conn:
        return repository.station_state(conn)


@router.post("/next")
def infeed_next():
    with repository.connection() as conn:
        inspection_id = repository.infeed_next(conn)
        if inspection_id is None:
            return {"done": True, "inspection": None}
        return JSONResponse(
            status_code=201,
            content={"done": False, "inspection": repository.inspection_summary(conn, inspection_id)},
        )


@router.post("/parts/{part_id}/infeed", status_code=201)
def infeed_part(part_id: int):
    with repository.connection() as conn:
        inspection_id = repository.infeed_part(conn, part_id)
        return {"inspection": repository.inspection_summary(conn, inspection_id)}


@router.get("/inspections/{inspection_id}")
def inspection(inspection_id: int):
    with repository.connection() as conn:
        return repository.inspection_detail(conn, inspection_id)


@router.get("/inspections/{inspection_id}/image")
def inspection_image(inspection_id: int):
    with repository.connection() as conn:
        path = repository.image_path(conn, inspection_id)
    return FileResponse(path, media_type="image/png")


@router.post("/inspections/{inspection_id}/review", status_code=201)
def review(inspection_id: int, body: ReviewIn):
    with repository.connection() as conn:
        repository.save_review(
            conn,
            inspection_id,
            body.decision,
            body.final_verdict,
            body.comment,
            [c.model_dump() for c in body.changes],
        )
        return repository.inspection_detail(conn, inspection_id)
