from .base import BaseAgent
from ..utils.prompt_templates import PLANNING_PROMPT, OUTLINE_REVISE_PROMPT, CONSTRAINT_EXTRACTION_PROMPT
from ..utils.json_parser import parse_json
from ..utils.style_brief import StyleSummarizer
from ..config import settings
import json as _json


class Planner(BaseAgent):
    """规划师：生成大纲 + 根据反馈修订。"""

    def generate_outline(
        self, topic: str, style: dict, target_words: int = 10000,
        world_setting: str = "", story_synopsis: str = "",
    ) -> list[dict]:
        """生成初版大纲（含小节结构）。"""
        subs_per_section = max(3, min(12, target_words // 2000))
        subsection_words = target_words // subs_per_section

        ws_text = f"## 世界观设定\n{world_setting}" if world_setting.strip() else ""
        ss_text = f"## 故事梗概\n{story_synopsis}" if story_synopsis.strip() else ""

        style_brief = style.get("style_brief", "") if isinstance(style, dict) else ""
        style_structured = StyleSummarizer.for_planner(style) if isinstance(style, dict) else ""

        prompt = PLANNING_PROMPT.format(
            topic=topic,
            target_words=target_words,
            subsection_words=subsection_words,
            subsections_per_section=subs_per_section,
            world_setting=ws_text,
            story_synopsis=ss_text,
            style_structured=style_structured,
            style_brief=style_brief if style_brief else f"情感强度{style.get('emotion_intensity', 50)}/100",
        )

        messages = [
            {"role": "system", "content": "你是一位文章策划编辑。请输出 JSON 对象，包含 outline 数组。"},
            {"role": "user", "content": prompt},
        ]
        response = self.llm.chat_completion(messages, temperature=0.5, max_tokens=4000)
        self.last_raw_response = response

        try:
            result = parse_json(response)
            if isinstance(result, dict) and "outline" in result and isinstance(result["outline"], list):
                outline = result["outline"]
            elif isinstance(result, list):
                outline = result
            else:
                raise ValueError("LLM 返回了意外格式，无法提取 outline 列表")
            if not outline or not isinstance(outline[0], dict):
                raise ValueError("outline 为空或格式不正确")
            return self._normalize_outline(outline, subs_per_section, subsection_words)
        except (ValueError, TypeError, KeyError):
            raise ValueError(f"大纲规划 JSON 解析失败。原始响应:\n{response[:800]}")

    def revise_from_feedback(
        self, topic: str, original_outline: list[dict], feedback_text: str,
        target_words_per_section: int = 10000,
    ) -> list[dict]:
        """根据审查意见修订大纲（仅 1 轮）。"""
        import json
        outline_text = json.dumps(original_outline, ensure_ascii=False, indent=2)

        prompt = OUTLINE_REVISE_PROMPT.format(
            topic=topic,
            outline_text=outline_text,
            feedback_text=feedback_text,
        )

        messages = [
            {"role": "system", "content": "你是一位文章策划编辑。请输出修订后的完整 JSON 大纲。"},
            {"role": "user", "content": prompt},
        ]
        response = self.llm.chat_completion(messages, temperature=0.4, max_tokens=4000)
        self.last_raw_response = response

        try:
            result = parse_json(response)
            if isinstance(result, dict) and "outline" in result and isinstance(result["outline"], list):
                outline = result["outline"]
            elif isinstance(result, list):
                outline = result
            else:
                return original_outline
            if not outline or not isinstance(outline[0], dict):
                return original_outline
            first_sec = original_outline[0] if original_outline else {}
            orig_subs = first_sec.get("subsections", [])
            target_subs = len(orig_subs) if orig_subs else max(3, min(12, target_words_per_section // 2000))
            sub_words = orig_subs[0].get("target_words", 2000) if orig_subs else max(500, target_words_per_section // max(1, target_subs))
            return self._normalize_outline(outline, target_subs, sub_words)
        except (ValueError, TypeError, KeyError):
            # 解析失败则沿用原大纲
            return original_outline

    def _normalize_outline(
        self, outline: list[dict], target_subs: int, target_words: int
    ) -> list[dict]:
        """确保每节的小节数和字数目标符合要求。"""
        for sec in outline:
            subs = sec.get("subsections", [])
            if len(subs) < target_subs:
                template = subs[-1] if subs else {"title": "续", "key_points": ["继续展开"]}
                for i in range(len(subs) + 1, target_subs + 1):
                    subs.append({
                        "subsection": i,
                        "title": f"{template.get('title', '续')}（续）",
                        "key_points": template.get("key_points", ["继续展开"])[:],
                        "target_words": target_words,
                    })
            elif len(subs) > target_subs:
                subs = subs[:target_subs]

            for i, sub in enumerate(subs, 1):
                sub["subsection"] = i
                sub["target_words"] = target_words

            sec["subsections"] = subs
        return outline

    # 兼容旧接口
    def run(self, topic: str, style: dict, target_words: int = 10000) -> list[dict]:
        return self.generate_outline(topic, style, target_words)

    def extract_constraints(
        self, topic: str, outline: list[dict], world_setting: str = ""
    ) -> list[dict]:
        """从已生成的大纲中提取故事线约束。"""
        outline_text = _json.dumps(outline, ensure_ascii=False, indent=2)
        prompt = CONSTRAINT_EXTRACTION_PROMPT.format(
            topic=topic,
            outline_text=outline_text,
            world_setting=world_setting if world_setting.strip() else "（无）",
        )
        messages = [
            {"role": "system", "content": "你是一位严谨的故事结构专家。请输出 JSON 数组。"},
            {"role": "user", "content": prompt},
        ]
        try:
            response = self.llm.chat_completion(messages, temperature=0.3, max_tokens=2000, json_mode=True)
            result = parse_json(response)
            if isinstance(result, list):
                # 给每个约束生成 ID
                import uuid
                for c in result:
                    if isinstance(c, dict) and not c.get("id"):
                        c["id"] = str(uuid.uuid4())
                return result
            return []
        except Exception:
            return []
