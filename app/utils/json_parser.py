import json
import re


def parse_json(response: str) -> dict | list:
    """从 LLM 返回的文本中鲁棒地提取 JSON 对象或数组。

    处理常见情况：
    - 纯 JSON 字符串
    - ```json ... ``` 包裹的 markdown 代码块
    - JSON 前后有额外文字说明
    - 尾逗号（LLM 高频错误）
    - 截断的 JSON（尝试自动补全）
    - 单引号 JSON（部分模型偏好）
    """
    text = response.strip()

    # 1. 移除 markdown 代码块
    if text.startswith("```"):
        lines = text.split("\n")
        if len(lines) > 1:
            text = "\n".join(lines[1:])
        if text.rstrip().endswith("```"):
            text = text.rstrip()[: text.rstrip().rfind("```")].strip()

    # 2. 尝试直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 3. 提取 JSON 块并容错解析
    for candidate in _extract_json_candidates(text):
        result = _try_recover(candidate)
        if result is not None:
            return result

    # 4. 单引号 JSON fallback
    for candidate in _extract_json_candidates(text):
        result = _try_json_with_single_quotes(candidate)
        if result is not None:
            return result

    raise ValueError(
        f"无法从以下响应中提取 JSON:\n{text[:500]}..."
    )


def _try_recover(json_str: str) -> dict | list | None:
    """尝试多种容错策略解析 JSON 字符串。"""
    # 策略 1：去除尾逗号
    cleaned = _strip_trailing_commas(json_str)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # 策略 2：截断自动补全
    try:
        completed = _auto_close_brackets(cleaned)
        return json.loads(completed)
    except json.JSONDecodeError:
        pass

    return None


def _try_json_with_single_quotes(json_str: str) -> dict | list | None:
    """尝试将单引号替换为双引号后解析。"""
    try:
        # Python 的 ast.literal_eval 可以解析单引号 dict/list
        import ast
        return ast.literal_eval(json_str)
    except (ValueError, SyntaxError):
        pass

    # fallback: 简单替换（注意避免字符串内部的双引号问题）
    try:
        fixed = json_str.replace("'", '"')
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass

    return None


def _strip_trailing_commas(json_str: str) -> str:
    """移除 JSON 对象和数组中的尾逗号。"""
    # 移除 }, 或 ,} 或 ], 或 ,] 中的尾逗号
    cleaned = re.sub(r',(\s*[}\]])', r'\1', json_str)
    return cleaned


def _auto_close_brackets(json_str: str) -> str:
    """尝试自动补全未闭合的括号和引号。"""
    # 统计未闭合的括号
    open_braces = json_str.count("{") - json_str.count("}")
    open_brackets = json_str.count("[") - json_str.count("]")

    # 检查是否在字符串中间被截断
    in_string = False
    for ch in json_str:
        if ch == '"':
            in_string = not in_string
    if in_string:
        json_str += '"'

    # 补全括号
    json_str += "]" * open_brackets
    json_str += "}" * open_braces

    return json_str


def _extract_json_candidates(text: str) -> list[str]:
    """从文本中提取可能的 JSON 片段（平衡的或截断的）。"""
    candidates = []
    for open_char, close_char in (("{", "}"), ("[", "]")):
        balanced = _extract_balanced(text, open_char, close_char)
        if balanced is not None:
            candidates.append(balanced)
        else:
            # 有开头无结尾（截断），手动从开头取到尾
            start = text.find(open_char)
            if start != -1:
                candidates.append(text[start:])
    return candidates


def _extract_balanced(text: str, open_char: str, close_char: str) -> str | None:
    """从文本中提取第一个平衡的括号内容。"""
    start = text.find(open_char)
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape_next = False

    for i in range(start, len(text)):
        ch = text[i]

        if escape_next:
            escape_next = False
            continue

        if ch == "\\":
            escape_next = True
            continue

        if ch == '"' and not escape_next:
            in_string = not in_string
            continue

        if in_string:
            continue

        if ch == open_char:
            depth += 1
        elif ch == close_char:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]

    return None
