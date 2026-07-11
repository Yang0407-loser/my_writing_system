"""测试 Planner。"""

import json
from app.agents.planner import Planner


class TestNormalizeOutline:
    def test_pads_missing_subsections(self):
        p = Planner()
        outline = [
            {
                "section": 1, "title": "开端", "key_points": [],
                "subsections": [
                    {"subsection": 1, "title": "唯一", "key_points": [], "target_words": 1500},
                ],
            },
        ]
        result = p._normalize_outline(outline, target_subs=3, target_words=1500)
        assert len(result[0]["subsections"]) == 3
        assert result[0]["subsections"][0]["subsection"] == 1
        assert result[0]["subsections"][2]["subsection"] == 3

    def test_truncates_extra_subsections(self):
        p = Planner()
        outline = [
            {
                "section": 1, "title": "开端", "key_points": [],
                "subsections": [
                    {"subsection": 1, "title": f"第{i}节", "key_points": [], "target_words": 1500}
                    for i in range(1, 6)
                ],
            },
        ]
        result = p._normalize_outline(outline, target_subs=3, target_words=1500)
        assert len(result[0]["subsections"]) == 3

    def test_fixes_subsection_numbering(self):
        p = Planner()
        outline = [
            {
                "section": 1, "title": "开端", "key_points": [],
                "subsections": [
                    {"subsection": 5, "title": "错号", "key_points": [], "target_words": 999},
                    {"subsection": 99, "title": "乱号", "key_points": [], "target_words": 888},
                ],
            },
        ]
        result = p._normalize_outline(outline, target_subs=2, target_words=1500)
        assert result[0]["subsections"][0]["subsection"] == 1
        assert result[0]["subsections"][1]["subsection"] == 2
        assert result[0]["subsections"][0]["target_words"] == 1500
