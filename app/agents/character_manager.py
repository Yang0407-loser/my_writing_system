import copy
import uuid
import json
from .base import BaseAgent
from ..utils.prompt_templates import (
    CHARACTER_EXTRACTION_PROMPT,
    CHARACTER_ARC_PROMPT,
    CHARACTER_STATE_UPDATE_PROMPT,
)
from ..utils.json_parser import parse_json


class CharacterManager(BaseAgent):
    """角色管理器：提取、弧线规划、状态更新。

    不重载 run() —— 由 coordinator 直接调用专用方法。
    """

    def run(self, **kwargs) -> dict:
        raise NotImplementedError("使用 extract_characters / plan_arcs / update_states 替代")

    def extract_characters(self, character_text: str) -> list[dict]:
        """从自然语言描述中提取结构化角色列表。

        Args:
            character_text: 用户输入的自由文本人物描述。

        Returns:
            list[dict]: 角色卡列表，每个含 id/name/personality 等字段。
        """
        if not character_text.strip():
            return []

        prompt = CHARACTER_EXTRACTION_PROMPT.format(character_text=character_text)
        messages = [
            {"role": "system", "content": "你是一位专业的人物设定编辑。请严格以 JSON 数组格式输出。"},
            {"role": "user", "content": prompt},
        ]
        response = self.llm.chat_completion(messages, temperature=0.3, max_tokens=4000)
        self.last_raw_response = response

        try:
            result = parse_json(response)
            if isinstance(result, dict):
                result = [result]
            if not isinstance(result, list):
                raise ValueError(f"LLM 返回了非数组格式: {type(result)}")
        except ValueError:
            raise ValueError(
                f"角色提取 JSON 解析失败。原始响应:\n{response[:800]}"
            )

        # 确保每个角色有 id
        for char in result:
            if not char.get("id"):
                char["id"] = str(uuid.uuid4())
        return result

    def plan_arcs(self, characters: list[dict], outline: list[dict]) -> list[dict]:
        """根据角色卡和大纲规划每个角色的变化弧线。

        Args:
            characters: 角色卡列表。
            outline: 大纲 V2。

        Returns:
            list[dict]: 角色弧线列表。
        """
        if not characters:
            return []

        characters_json = json.dumps(characters, ensure_ascii=False, indent=2)
        outline_json = json.dumps(outline, ensure_ascii=False, indent=2)

        prompt = CHARACTER_ARC_PROMPT.format(
            characters_json=characters_json,
            outline_json=outline_json,
        )
        messages = [
            {"role": "system", "content": "你是一位故事结构专家。请严格以 JSON 数组格式输出。"},
            {"role": "user", "content": prompt},
        ]
        response = self.llm.chat_completion(messages, temperature=0.4, max_tokens=4000)
        self.last_raw_response = response

        try:
            result = parse_json(response)
            if isinstance(result, dict):
                result = [result]
            if not isinstance(result, list):
                raise ValueError(f"LLM 返回了非数组格式: {type(result)}")
        except ValueError:
            return []

        # 确保 current_state 初始等于 starting_state
        for arc in result:
            if not arc.get("current_state"):
                arc["current_state"] = arc.get("starting_state", "")
        return result

    def update_states(
        self, characters: list[dict], arcs: list[dict],
        section_text: str, section_num: int,
        max_batch: int = 15,
    ) -> list[dict]:
        """根据刚写好的正文批量更新所有角色的当前状态。

        单次 LLM 调用处理所有角色（≤max_batch），超过则分批。
        """
        if not arcs:
            return arcs

        updated_arcs = [copy.deepcopy(a) for a in arcs]

        # 按角色分组
        arc_char_pairs = []
        for arc in updated_arcs:
            char_id = arc.get("character_id", "")
            char = next((c for c in characters if c.get("id") == char_id), None)
            if char:
                arc_char_pairs.append((arc, char))

        if not arc_char_pairs:
            return updated_arcs

        # 分批处理
        for batch_start in range(0, len(arc_char_pairs), max_batch):
            batch = arc_char_pairs[batch_start:batch_start + max_batch]
            characters_json = json.dumps(
                [{"name": c.get("name", "?"), "personality": c.get("personality", []),
                  "motivation": c.get("motivation", ""), "catchphrase": c.get("catchphrase", ""),
                  "current_state": a.get("current_state", ""),
                  "arc_start": a.get("starting_state", ""), "arc_end": a.get("ending_state", ""),
                  "character_id": a.get("character_id", "")}
                 for a, c in batch],
                ensure_ascii=False, indent=2,
            )

            prompt = CHARACTER_STATE_UPDATE_PROMPT.format(
                characters_json=characters_json,
                section_num=section_num,
                section_text=section_text[:8000],
            )
            messages = [
                {"role": "system", "content": "你是一位细心的角色跟踪编辑。请严格以 JSON 数组格式输出。"},
                {"role": "user", "content": prompt},
            ]
            response = self.llm.chat_completion(messages, temperature=0.3, max_tokens=min(4000, 500 * len(batch)))
            self.last_raw_response = response

            try:
                results = parse_json(response)
                if isinstance(results, dict):
                    results = [results]
                if isinstance(results, list):
                    for item in results:
                        cid = item.get("character_id", "")
                        new_state = item.get("current_state", "")
                        if new_state:
                            for arc in updated_arcs:
                                if arc.get("character_id") == cid:
                                    arc["current_state"] = new_state
                                    break
            except ValueError:
                pass

        return updated_arcs
