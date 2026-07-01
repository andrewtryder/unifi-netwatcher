from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.scanner import run_scan

router = APIRouter()

@router.post("/run")
def trigger_scan(db: Session = Depends(get_db)):
    result = run_scan(db, source="manual")
    return {"status": "scan complete", **result}
