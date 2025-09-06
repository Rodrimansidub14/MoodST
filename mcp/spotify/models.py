# models.py
from typing import List, Optional
from pydantic import BaseModel, Field

class Artist(BaseModel):
    name: str

class Track(BaseModel):
    id: str
    name: str
    artists: List[Artist]
    uri: str
    preview_url: Optional[str] = None

class Mood(BaseModel):
    mood: str
    valence: float = Field(ge=0.0, le=1.0)
    energy: float = Field(ge=0.0, le=1.0)
    tags: List[str] = []

class ExplainContext(BaseModel):
    mood: Optional[str] = None
    activity: Optional[str] = None
    time_of_day: Optional[str] = None

class PlaylistRef(BaseModel):
    playlist_id: str
    url: str

class EnsureDeviceResult(BaseModel):
    device_id: Optional[str]
    status: str  # "no_devices", "ready", "transferred", "not_premium"

class AddedResult(BaseModel):
    added: int

class PlayResult(BaseModel):
    status: str  # "playing", "unsupported", "not_premium", "no_device"
    device_id: Optional[str] = None
