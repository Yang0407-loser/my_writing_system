from .base import BaseAgent
from ..utils.prompt_templates import CONTINUITY_EDITOR_PROMPT
from ..utils.json_parser import parse_json


class ContinuityEditor(BaseAgent):
    """连续性编辑：汇总 Writer 在写作过程中发现的前后矛盾，判断哪些必须修正。

    类似 git merge 的 conflict resolver——不是所有建议都执行，只处理严重的。
    """

    def run(
        self,
        backref_suggestions: list[dict],
        section_summaries: str,
    ) -> dict:
        """汇总回溯修正建议，输出分级修正清单。

        Returns:
            {critical_fixes: [...], minor_fixes: [...], summary: str}
        """
        # 格式化回溯建议
        sug_text = "\n".join(
            f"- [第{s.get('from_section', '?')}节提出] 目标第{s.get('target_section', '?')}节: {s.get('description', '')}"
            for s in backref_suggestions
        )
        if not sug_text:
            sug_text = "（无回溯修正建议）"

        prompt = CONTINUITY_EDITOR_PROMPT.format(
            backref_suggestions=sug_text,
            section_summaries=section_summaries,
        )

        messages = [
            {"role": "system", "content": "你是一位严谨的连续性编辑。请以 JSON 格式输出修正清单。"},
            {"role": "user", "content": prompt},
        ]
        response = self.llm.chat_completion(messages, temperature=0.3, max_tokens=2000)
        self.last_raw_response = response

        try:
            result = parse_json(response)
            return {
                "critical_fixes": result.get("critical_fixes", []),
                "minor_fixes": result.get("minor_fixes", []),
                "summary": result.get("summary", ""),
            }
        except ValueError:
            # 如果解析失败，返回空清单（不阻塞流程）
            return {
                "critical_fixes": [],
                "minor_fixes": [],
                "summary": "（连续性编辑未生成有效输出）",
            }
