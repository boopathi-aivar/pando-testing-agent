from fastapi import APIRouter, Depends, HTTPException, status

from models.project import ProjectConfig, ProjectCreate, ProjectUpdate
from database import col_projects, col_results, col_jobs
from routers.auth import get_current_user

router = APIRouter(tags=["projects"])


@router.get("/projects", response_model=list[ProjectConfig])
def list_projects(_user=Depends(get_current_user)):
    return list(col_projects().find({}, {"_id": 0}))


@router.get("/projects/{project_id}", response_model=ProjectConfig)
def get_project(project_id: str, _user=Depends(get_current_user)):
    project = col_projects().find_one({"project_id": project_id}, {"_id": 0})
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Project '{project_id}' not found")
    return project


@router.post("/projects", response_model=ProjectConfig, status_code=status.HTTP_201_CREATED)
def create_project(body: ProjectCreate, _user=Depends(get_current_user)):
    if col_projects().find_one({"project_id": body.project_id}):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Project '{body.project_id}' already exists")
    doc = body.model_dump()
    doc.setdefault("status", "configured")
    doc.setdefault("last_tested", None)
    doc.setdefault("last_score", None)
    col_projects().insert_one(doc)
    doc.pop("_id", None)
    return doc


@router.put("/projects/{project_id}", response_model=ProjectConfig)
def update_project(project_id: str, body: ProjectUpdate, _user=Depends(get_current_user)):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No fields to update")
    result = col_projects().find_one_and_update(
        {"project_id": project_id},
        {"$set": updates},
        projection={"_id": 0},
        return_document=True,
    )
    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Project '{project_id}' not found")
    return result


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: str, _user=Depends(get_current_user)):
    """Delete a project and all its associated results and jobs."""
    result = col_projects().delete_one({"project_id": project_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Project '{project_id}' not found")
    col_results().delete_many({"project_id": project_id})
    col_jobs().delete_many({"project_id": project_id})
