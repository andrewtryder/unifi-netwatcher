from pydantic import BaseModel
from typing import Optional

class RenameRequest(BaseModel):
    display_name: str

class NotesRequest(BaseModel):
    notes: str
