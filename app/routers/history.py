"""任务历史 API。"""

from fastapi import APIRouter, HTTPException
from ..task_store import TaskStore

router = APIRouter(tags=["history"])


@router.get("/tasks")
def list_tasks(limit: int = 50):
    """列出历史任务。"""
    ts = TaskStore()
    tasks = ts.list_all(limit=limit)
    return {"tasks": tasks, "total": len(tasks)}


@router.get("/tasks/{task_id}/history")
def get_task_history(task_id: str):
    """获取单个任务的历史记录详情。"""
    ts = TaskStore()
    task = ts.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"task": task}


@router.delete("/tasks/{task_id}/history")
def delete_task_history(task_id: str):
    """删除任务历史记录。"""
    ts = TaskStore()
    if not ts.delete(task_id):
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"status": "deleted"}
