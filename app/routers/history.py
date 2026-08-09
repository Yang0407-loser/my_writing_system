"""任务历史 API。"""

from fastapi import APIRouter, HTTPException
from ..task_store import TaskStore

router = APIRouter(tags=["history"])


@router.get("/tasks")
def list_tasks(limit: int = 50):
    """列出历史任务。"""
    with TaskStore() as ts:
        tasks = ts.list_all(limit=limit)
    return {"tasks": tasks, "total": len(tasks)}


@router.get("/tasks/{task_id}/history")
def get_task_history(task_id: str):
    """获取单个任务的历史记录详情。"""
    with TaskStore() as ts:
        task = ts.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"task": task}


@router.delete("/tasks/{task_id}/history")
def delete_task_history(task_id: str):
    """删除任务历史记录。"""
    with TaskStore() as ts:
        deleted = ts.delete(task_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"status": "deleted"}
