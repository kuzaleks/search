from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    StringConstraints,
)


SocialLink = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=2048),
]


class ClientCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    description: str | None = None
    social_links: list[SocialLink] = Field(default_factory=list, max_length=20)


class ClientResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    first_name: str
    last_name: str
    email: EmailStr
    description: str | None
    social_links: list[str]


class DocumentCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=500)
    content: str = Field(min_length=1, max_length=100_000)


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    client_id: UUID
    title: str
    content: str
    created_at: datetime


class ClientSearchResult(BaseModel):
    score: float
    client: ClientResponse


class DocumentSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    client_id: UUID
    title: str
    created_at: datetime


class DocumentSearchResult(BaseModel):
    score: float
    document: DocumentSummary
    snippet: str


class SearchResponse(BaseModel):
    query: str
    clients: list[ClientSearchResult]
    documents: list[DocumentSearchResult]
