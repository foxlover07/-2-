from fastapi import APIRouter, HTTPException

from app.data import get_property, load_properties
from app.models import Property

router = APIRouter(prefix="/properties", tags=["properties"])


@router.get("", response_model=list[Property])
def list_properties() -> list[Property]:
    return list(load_properties())


@router.get("/{property_id}", response_model=Property)
def read_property(property_id: str) -> Property:
    prop = get_property(property_id)
    if prop is None:
        raise HTTPException(status_code=404, detail=f"Property {property_id} not found")
    return prop
