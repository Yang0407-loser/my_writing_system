class CharacterFormatter:
    """统一角色上下文格式化 —— 消除 Writer / Reviewer / Coordinator 中的重复实现。"""

    @staticmethod
    def build_context(characters: list[dict] | None, arcs: list[dict] | None = None) -> str:
        """构建注入 prompt 的角色描述文本（五层模型）。"""
        if not characters:
            return "（无人物设定）"

        lines = []
        for c in characters:
            name = c.get("name", "?")
            # 表层
            parts = [name]
            surface_info = []
            if c.get("gender"): surface_info.append(c["gender"])
            if c.get("age"): surface_info.append(str(c["age"]))
            if surface_info: parts.append("（" + "，".join(surface_info) + "）")
            if c.get("appearance"): parts.append("\n  外表: " + c["appearance"][:60])
            if c.get("catchphrase"): parts.append("\n  口头禅: " + c["catchphrase"][:40])
            # 性格层
            personality = "、".join(c.get("personality", [])) or None
            strengths = "、".join(c.get("strengths", [])) or None
            weaknesses = "、".join(c.get("weaknesses", [])) or None
            if personality: parts.append("\n  性格: " + personality)
            if strengths: parts.append("\n  优点: " + strengths)
            if weaknesses: parts.append("\n  弱点: " + weaknesses)
            # 动机层
            if c.get("motivation"): parts.append("\n  动机: " + c["motivation"])
            if c.get("background"): parts.append("\n  背景: " + c["background"][:120])
            if c.get("world_position"): parts.append("\n  定位: " + c["world_position"][:80])
            # 隐层（有内容才显示）
            hidden = []
            if c.get("secret"): hidden.append("隐藏身份: " + c["secret"][:100])
            if c.get("previous_life"): hidden.append("前世: " + c["previous_life"][:100])
            if c.get("previous_world"): hidden.append("前世世界: " + c["previous_world"][:80])
            if c.get("preserved_knowledge"): hidden.append("保留记忆/技能: " + c["preserved_knowledge"][:100])
            if c.get("identity_conflict"): hidden.append("身份冲突: " + c["identity_conflict"][:100])
            if hidden:
                parts.append("\n  【隐层】")
                for h in hidden:
                    parts.append("\n    · " + h)
            # 弧线状态
            if arcs:
                arc = next((a for a in arcs if a.get("character_id") == c.get("id")), None)
                if arc and arc.get("current_state"):
                    parts.append(f"\n  弧线状态: {arc['current_state']}")

            lines.append("".join(parts))

        return "\n\n".join(lines)

    @staticmethod
    def build_arc_context(characters: list[dict] | None, arcs: list[dict] | None = None,
                          section: int = 0, subsection: int = 0) -> str:
        """构建角色弧线要求文本——支持按小节过滤。

        若指定 section/subsection，只输出该小节的里程碑详情；
        否则输出全篇弧线概览。
        """
        if not arcs:
            return "（无弧线要求）"

        if section > 0:
            return CharacterFormatter._build_section_timeline(characters, arcs, section, subsection)

        # 全篇概览模式
        lines = []
        for a in arcs:
            char = next((c for c in (characters or []) if c.get("id") == a.get("character_id")), None)
            name = char.get("name", "?") if char else "?"
            milestones = a.get("key_milestones", [])
            if milestones:
                ms_parts = []
                for m in milestones:
                    s = m.get("section", "?")
                    sub = m.get("subsection", "?")
                    event = m.get("event", "?")
                    location = m.get("location", "")
                    time = m.get("time", "")
                    emo = m.get("emotional_shift", "")
                    detail = f"第{s}节·第{sub}小节「{event}」"
                    if location:
                        detail += f" 📍{location}"
                    if time:
                        detail += f" 🕐{time}"
                    if emo:
                        detail += f" 💭{emo}"
                    ms_parts.append(detail)
                lines.append(f"- {name}（{a.get('starting_state','?')} → {a.get('ending_state','?')}）：\n    " + "\n    ".join(ms_parts))

        result = "\n".join(lines) if lines else "（无弧线要求）"
        return result

    @staticmethod
    def _build_section_timeline(characters, arcs, section, subsection) -> str:
        """构建指定小节的角色出场时间线。"""
        lines = []
        for a in arcs:
            char = next((c for c in (characters or []) if c.get("id") == a.get("character_id")), None)
            if not char:
                continue
            name = char.get("name", "?")
            # 找匹配当前小节的里程碑
            matches = [
                m for m in a.get("key_milestones", [])
                if m.get("section") == section and m.get("subsection") == subsection
            ]
            if not matches:
                # 找最近的前一个里程碑（角色当前状态）
                prev = None
                for m in sorted(a.get("key_milestones", []), key=lambda x: (x.get("section",0), x.get("subsection",0))):
                    if (m.get("section",0) < section) or (m.get("section",0) == section and m.get("subsection",0) < subsection):
                        prev = m
                if prev:
                    lines.append(f"- {name}: 此前在「{prev.get('event','?')}」（{prev.get('location','?')}），当前处于{prev.get('emotional_shift','?').split('→')[-1] if '→' in prev.get('emotional_shift','') else a.get('current_state','?')}")
                else:
                    lines.append(f"- {name}: 本小节暂未出场（当前状态：{a.get('current_state', a.get('starting_state', '?'))}）")
            else:
                for m in matches:
                    parts = [f"- {name}: 【本小节关键事件】{m.get('event','?')}"]
                    if m.get("location"):
                        parts.append(f"  地点: {m['location']}")
                    if m.get("time"):
                        parts.append(f"  时间: {m['time']}")
                    if m.get("emotional_shift"):
                        parts.append(f"  情感转折: {m['emotional_shift']}")
                    lines.append("\n".join(parts))

        return "\n".join(lines) if lines else "（本小节无角色出场安排）"

    @staticmethod
    def format_for_consistency_check(characters: list[dict] | None) -> str:
        """构建角色一致性检查用的角色 JSON 摘要。

        用于 Reviewer / ContinuityEditor 的人物一致性评价。
        """
        if not characters:
            return "[]"

        import json
        summary = []
        for c in characters:
            summary.append({
                "name": c.get("name", "?"),
                "personality": c.get("personality", []),
                "motivation": c.get("motivation", ""),
                "catchphrase": c.get("catchphrase", ""),
                "secret": c.get("secret", ""),
            })
        return json.dumps(summary, ensure_ascii=False, indent=2)
