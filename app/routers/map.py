"""故事地图 API。"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from .. import map_manager as mm

router = APIRouter(prefix="/api/map", tags=["map"])


class NodeBody(BaseModel):
    task_id: str = ""
    name: str = ""
    type: str = "city"
    parent_id: str = ""
    description: str = ""
    x: float = 0
    y: float = 0
    properties: dict = {}


class EdgeBody(BaseModel):
    task_id: str = ""
    source_id: str = ""
    target_id: str = ""
    type: str = "road"
    name: str = ""
    travel_time: str = ""
    distance: str = ""


class RouteBody(BaseModel):
    task_id: str = ""
    chapter_start: int = 1
    chapter_end: int = 100
    path_nodes: list[str] = []


@router.get("/full")
def full_map(task_id: str = Query(...)):
    return mm.get_full_map(task_id)


@router.get("/nodes")
def list_nodes(task_id: str = Query(""), parent_id: str = Query("")):
    nodes = mm.list_nodes(task_id, parent_id)
    return {"nodes": nodes, "total": len(nodes)}


@router.get("/nodes/{node_id}")
def get_node(node_id: str):
    node = mm.get_node(node_id)
    if not node:
        raise HTTPException(status_code=404, detail="节点不存在")
    return node


@router.post("/nodes")
def create_node(body: NodeBody):
    return mm.create_node(body.model_dump())


@router.get("/edges")
def list_edges(task_id: str = Query("")):
    edges = mm.list_edges(task_id)
    return {"edges": edges, "total": len(edges)}


@router.post("/edges")
def create_edge(body: EdgeBody):
    return mm.create_edge(body.model_dump())


@router.get("/route")
def get_route(task_id: str = Query(...)):
    route = mm.get_protagonist_route(task_id)
    if not route:
        raise HTTPException(status_code=404, detail="未找到路线")
    return route


@router.post("/route")
def set_route(body: RouteBody):
    return mm.set_protagonist_route(
        body.task_id, body.chapter_start, body.chapter_end, body.path_nodes
    )
