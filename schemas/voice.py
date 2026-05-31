from pydantic import BaseModel

class GenerateAudioRequest(BaseModel):
    voice_id: int
    text: str