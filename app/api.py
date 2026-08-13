from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models import Client
from app.schemas import ClientCreate, ClientResponse


router = APIRouter()
DatabaseSession = Annotated[AsyncSession, Depends(get_session)]


@router.post(
    "/clients",
    response_model=ClientResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_409_CONFLICT: {
            "description": "A client with this email already exists"
        }
    },
    tags=["clients"],
)
async def create_client(
    client_data: ClientCreate,
    session: DatabaseSession,
) -> Client:
    client = Client(
        first_name=client_data.first_name,
        last_name=client_data.last_name,
        email=str(client_data.email).lower(),
        description=client_data.description,
        social_links=client_data.social_links,
    )
    session.add(client)

    try:
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A client with this email already exists",
        ) from error

    return client
