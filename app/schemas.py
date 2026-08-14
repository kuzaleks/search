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

    first_name: str = Field(
        min_length=1,
        max_length=100,
        description="Client's first name",
        examples=["John"],
    )
    last_name: str = Field(
        min_length=1,
        max_length=100,
        description="Client's last name",
        examples=["Doe"],
    )
    email: EmailStr = Field(
        description="Case-insensitively unique client email address",
        examples=["john.doe@example.com"],
    )
    description: str | None = Field(
        default=None,
        description="Optional searchable client description",
        examples=["Wealth management client"],
    )
    social_links: list[SocialLink] = Field(
        default_factory=list,
        max_length=20,
        description="Optional links to the client's social profiles",
    )


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

    title: str = Field(
        min_length=1,
        max_length=500,
        description="Searchable document title",
        examples=["Electricity statement"],
    )
    content: str = Field(
        min_length=1,
        max_length=100_000,
        description="Plain-text document content to index",
        examples=["Account holder John Doe. Service address: 10 High Street."],
    )


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


class ErrorIssue(BaseModel):
    field: str = Field(
        description="Location of the invalid value",
        examples=["body.email"],
    )
    message: str = Field(
        description="Human-readable validation failure",
        examples=["value is not a valid email address"],
    )
    code: str = Field(
        description="Machine-readable validation failure type",
        examples=["value_error"],
    )


class ErrorDetail(BaseModel):
    code: str = Field(
        description="Stable machine-readable application error code",
        examples=["client_not_found"],
    )
    message: str = Field(
        description="Human-readable error summary",
        examples=["Client not found"],
    )
    details: list[ErrorIssue] | None = Field(
        default=None,
        description="Field-level details supplied for validation failures",
    )


class ErrorResponse(BaseModel):
    error: ErrorDetail
