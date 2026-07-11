from abc import ABC, abstractmethod
from ..utils.llm_client import get_llm_client


class BaseAgent(ABC):
    """所有智能体的基类。提供 LLM 客户端的统一访问入口。"""

    def __init__(self):
        self.llm = get_llm_client()
        self.last_raw_response: str = ""

    @abstractmethod
    def run(self, **kwargs) -> dict:
        """执行智能体的核心任务。"""
        ...
