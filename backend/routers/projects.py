from fastapi import APIRouter, Depends, HTTPException, status

from models.project import ProjectConfig, ProjectCreate, ProjectUpdate
from database import tbl_projects, tbl_results, tbl_jobs
from tools.dynamodb_tools import _to_dynamo, _from_dynamo
from routers.auth import get_current_user

router = APIRouter(tags=["projects"])


@router.get("/projects", response_model=list[ProjectConfig])
def list_projects(_user=Depends(get_current_user)):
    resp = tbl_projects().scan()
    return [_from_dynamo(i) for i in resp.get("Items", [])]


@router.get("/projects/{project_id}", response_model=ProjectConfig)
def get_project(project_id: str, _user=Depends(get_current_user)):
    resp = tbl_projects().get_item(Key={"project_id": project_id})
    item = resp.get("Item")
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Project '{project_id}' not found")
    return _from_dynamo(item)


@router.post("/projects", response_model=ProjectConfig, status_code=status.HTTP_201_CREATED)
def create_project(body: ProjectCreate, _user=Depends(get_current_user)):
    existing = tbl_projects().get_item(Key={"project_id": body.project_id}).get("Item")
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Project '{body.project_id}' already exists")
    doc = body.model_dump()
    doc.setdefault("status", "configured")
    doc.setdefault("last_tested", None)
    doc.setdefault("last_score", None)
    # DynamoDB cannot store None — remove null-valued keys
    doc = {k: v for k, v in doc.items() if v is not None}
    tbl_projects().put_item(Item=_to_dynamo(doc))
    return _from_dynamo(doc)


@router.put("/projects/{project_id}", response_model=ProjectConfig)
def update_project(project_id: str, body: ProjectUpdate, _user=Depends(get_current_user)):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No fields to update")

    # Check existence
    existing = tbl_projects().get_item(Key={"project_id": project_id}).get("Item")
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Project '{project_id}' not found")

    safe = _to_dynamo(updates)
    set_expr = ", ".join(f"#f{i} = :v{i}" for i, k in enumerate(safe))
    names    = {f"#f{i}": k for i, k in enumerate(safe)}
    values   = {f":v{i}": v for i, (k, v) in enumerate(safe.items())}

    resp = tbl_projects().update_item(
        Key={"project_id": project_id},
        UpdateExpression=f"SET {set_expr}",
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
        ReturnValues="ALL_NEW",
    )
    return _from_dynamo(resp["Attributes"])


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: str, _user=Depends(get_current_user)):
    """Delete a project and all its associated results and jobs."""
    existing = tbl_projects().get_item(Key={"project_id": project_id}).get("Item")
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Project '{project_id}' not found")

    tbl_projects().delete_item(Key={"project_id": project_id})

    # Delete all results for this project
    from boto3.dynamodb.conditions import Key as DKey
    results_resp = tbl_results().query(
        IndexName="project_id-index",
        KeyConditionExpression=DKey("project_id").eq(project_id),
        ProjectionExpression="result_id",
    )
    with tbl_results().batch_writer() as batch:
        for item in results_resp.get("Items", []):
            batch.delete_item(Key={"result_id": item["result_id"]})

    # Delete all jobs for this project
    jobs_resp = tbl_jobs().scan(
        FilterExpression=DKey("project_id").eq(project_id),
        ProjectionExpression="job_id",
    )
    with tbl_jobs().batch_writer() as batch:
        for item in jobs_resp.get("Items", []):
            batch.delete_item(Key={"job_id": item["job_id"]})
