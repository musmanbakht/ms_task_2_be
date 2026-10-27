from datetime import date
from typing import Optional, List

from pydantic import BaseModel


class ClassificationMapRequest(BaseModel):
    date: date
    mine_id: Optional[str] = None


class ClassificationClassInfo(BaseModel):
    value: int
    name: str
    color: str


class ClassificationSceneInfo(BaseModel):
    sceneId: str
    date: str
    cloudCover: Optional[float] = None


class ClassificationMineInfo(BaseModel):
    mineId: str
    mineName: Optional[str] = None
    sceneCount: int
    scenes: List[ClassificationSceneInfo]


class ClassificationClassifierInfo(BaseModel):
    modelYear: int
    periodStartYear: int
    periodEndYear: int
    asset: str


class ClassificationImageryInfo(BaseModel):
    collection: str
    searchWindowDays: int
    cloudCoverThreshold: int
    preferredSceneCount: int
    minimumSceneCount: int
    compositeMethod: str


class ClassificationMapResponse(BaseModel):
    status: int
    requestedDate: str
    scope: str

    classifier: ClassificationClassifierInfo
    imagery: ClassificationImageryInfo

    classes: List[ClassificationClassInfo]
    mines: List[ClassificationMineInfo]

    tileUrl: str
    boundingBox: List[float]