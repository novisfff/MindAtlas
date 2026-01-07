from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.common.responses import ApiResponse
from app.database import get_db
from app.relation.schemas import (
    RelationRequest,
    RelationResponse,
    RelationTypeRequest,
    RelationTypeResponse,
)
from app.relation.service import RelationService
from app.relation.service_type import RelationTypeService

router = APIRouter(prefix="/api/relations", tags=["relations"])
type_router = APIRouter(prefix="/api/relation-types", tags=["relation-types"])


# Relation Type endpoints
@type_router.get("", response_model=ApiResponse)
def list_relation_types(db: Session = Depends(get_db)) -> ApiResponse:
    service = RelationTypeService(db)
    relation_types = service.find_all()
    return ApiResponse.ok([RelationTypeResponse.model_validate(rt).model_dump(by_alias=True) for rt in relation_types])


@type_router.get("/{id}", response_model=ApiResponse)
def get_relation_type(id: UUID, db: Session = Depends(get_db)) -> ApiResponse:
    service = RelationTypeService(db)
    relation_type = service.find_by_id(id)
    return ApiResponse.ok(RelationTypeResponse.model_validate(relation_type).model_dump(by_alias=True))


@type_router.post("", response_model=ApiResponse)
def create_relation_type(request: RelationTypeRequest, db: Session = Depends(get_db)) -> ApiResponse:
    service = RelationTypeService(db)
    relation_type = service.create(request)
    return ApiResponse.ok(RelationTypeResponse.model_validate(relation_type).model_dump(by_alias=True))


@type_router.put("/{id}", response_model=ApiResponse)
def update_relation_type(id: UUID, request: RelationTypeRequest, db: Session = Depends(get_db)) -> ApiResponse:
    service = RelationTypeService(db)
    relation_type = service.update(id, request)
    return ApiResponse.ok(RelationTypeResponse.model_validate(relation_type).model_dump(by_alias=True))


# Relation endpoints
@router.get("", response_model=ApiResponse)
def list_relations(db: Session = Depends(get_db)) -> ApiResponse:
    service = RelationService(db)
    relations = service.find_all()
    return ApiResponse.ok([RelationResponse.model_validate(r).model_dump(by_alias=True) for r in relations])


@router.get("/{id}", response_model=ApiResponse)
def get_relation(id: UUID, db: Session = Depends(get_db)) -> ApiResponse:
    service = RelationService(db)
    relation = service.find_by_id(id)
    return ApiResponse.ok(RelationResponse.model_validate(relation).model_dump(by_alias=True))


@router.get("/entry/{entry_id}", response_model=ApiResponse)
def get_relations_by_entry(entry_id: UUID, db: Session = Depends(get_db)) -> ApiResponse:
    service = RelationService(db)
    relations = service.find_by_entry(entry_id)
    return ApiResponse.ok([RelationResponse.model_validate(r).model_dump(by_alias=True) for r in relations])


@router.post("", response_model=ApiResponse)
def create_relation(request: RelationRequest, db: Session = Depends(get_db)) -> ApiResponse:
    service = RelationService(db)
    relation = service.create(request)
    return ApiResponse.ok(RelationResponse.model_validate(relation).model_dump(by_alias=True))


@router.put("/{id}", response_model=ApiResponse)
def update_relation(id: UUID, request: RelationRequest, db: Session = Depends(get_db)) -> ApiResponse:
    service = RelationService(db)
    relation = service.update(id, request)
    return ApiResponse.ok(RelationResponse.model_validate(relation).model_dump(by_alias=True))


@router.delete("/{id}", response_model=ApiResponse)
def delete_relation(id: UUID, db: Session = Depends(get_db)) -> ApiResponse:
    service = RelationService(db)
    service.delete(id)
    return ApiResponse.ok(None, "Relation deleted successfully")
