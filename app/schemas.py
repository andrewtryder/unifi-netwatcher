from pydantic import BaseModel


class RenameRequest(BaseModel):
    display_name: str


class NotesRequest(BaseModel):
    notes: str


class BulkDeviceIdsRequest(BaseModel):
    device_ids: list[int]
