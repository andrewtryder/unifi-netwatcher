import json
from fastapi import APIRouter, Depends, Form, HTTPException
from sqlalchemy.orm import Session
from fastapi.responses import HTMLResponse

from app.db import get_db
from app.models import NotificationChannel
from app.notifications import PROVIDERS

router = APIRouter()

@router.post("/htmx/create")
def create_channel(
    name: str = Form(...),
    type: str = Form(...),
    config_json: str = Form(...),
    db: Session = Depends(get_db)
):
    if type not in PROVIDERS:
        return HTMLResponse("<span class='text-red-500'>Invalid provider type</span>", status_code=400)
    try:
        conf = json.loads(config_json)
    except:
        return HTMLResponse("<span class='text-red-500'>Invalid JSON config</span>", status_code=400)
        
    channel = NotificationChannel(name=name, type=type, config_json=config_json)
    db.add(channel)
    db.commit()
    return HTMLResponse("<script>window.location.reload()</script>")

@router.post("/htmx/{channel_id}/delete")
def delete_channel(channel_id: int, db: Session = Depends(get_db)):
    channel = db.query(NotificationChannel).filter(NotificationChannel.id == channel_id).first()
    if channel:
        db.delete(channel)
        db.commit()
    return HTMLResponse("")

@router.post("/htmx/{channel_id}/test")
def test_channel(channel_id: int, db: Session = Depends(get_db)):
    channel = db.query(NotificationChannel).filter(NotificationChannel.id == channel_id).first()
    if not channel: return HTMLResponse("Not found", 404)
    
    provider = PROVIDERS.get(channel.type)
    if not provider: return HTMLResponse("Provider err", 400)
    
    success, sc, resp, err = provider.send("NetWatcher Test Message!", json.loads(channel.config_json))
    
    if success:
        return HTMLResponse("<span class='text-green-600 text-xs font-bold ml-2'>Test OK!</span>")
    else:
        return HTMLResponse(f"<span class='text-red-600 text-xs font-bold ml-2'>Failed: {err or sc}</span>")
