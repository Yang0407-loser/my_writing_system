"""
基础测试脚本：验证多智能体协作写作系统的核心功能。

运行前请确保：
1. Redis 已启动（docker-compose up -d）
2. .env 文件中已填入有效的 LLM_API_KEY
3. Celery worker 已启动（celery -A app.celery_app worker --loglevel=info）
4. FastAPI 已启动（uvicorn app.main:app --reload）
"""

import sys
import os
import json
import time
import requests

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.config import settings
from app.embedding.factory import get_embedding_provider


BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")


def test_embedding_provider():
    """测试 embedding 提供商是否能正常工作。"""
    print("=" * 60)
    print("[TEST] 测试 Embedding 提供商")
    print(f"  提供商类型: {settings.EMBEDDING_PROVIDER}")
    print(f"  模型名称: {settings.EMBEDDING_MODEL}")

    provider = get_embedding_provider()
    print(f"  实际模型: {provider.model_name}")
    print(f"  向量维度: {provider.dimension}")

    # 测试单条 embedding
    vec = provider.embed("你好世界")
    assert len(vec) == provider.dimension, f"维度不匹配: {len(vec)} != {provider.dimension}"
    print(f"  单条 embedding 测试通过 ✓")

    # 测试批量 embedding
    vecs = provider.embed_batch(["你好世界", "Hello world", "测试文本"])
    assert len(vecs) == 3
    assert all(len(v) == provider.dimension for v in vecs)
    print(f"  批量 embedding 测试通过 ✓")

    print("[PASS] Embedding 提供商测试全部通过\n")
    return provider


def test_llm_config():
    """测试 LLM 配置是否正确。"""
    print("=" * 60)
    print("[TEST] 测试 LLM 配置")
    print(f"  LLM 模型: {settings.LLM_MODEL}")
    print(f"  LLM Base URL: {settings.LLM_BASE_URL}")
    print(f"  LLM API Key: {'***' + settings.LLM_API_KEY[-4:] if settings.LLM_API_KEY else '未设置'}")

    if not settings.LLM_API_KEY:
        print("[WARN] LLM_API_KEY 未设置，跳过 LLM 调用测试")
        return False

    from app.utils.llm_client import get_llm_client

    try:
        llm = get_llm_client()
        resp = llm.chat_completion(
            messages=[{"role": "user", "content": "请用一句话介绍你自己。"}],
            temperature=0.3,
            max_tokens=100,
        )
        print(f"  LLM 回复: {resp[:80]}...")
        print("[PASS] LLM 配置测试通过\n")
        return True
    except Exception as e:
        print(f"[FAIL] LLM 调用失败: {e}\n")
        return False


def test_full_workflow():
    """测试完整的写作流程（需要 FastAPI 和 Celery worker 都在运行）。"""
    print("=" * 60)
    print("[TEST] 测试完整写作流程")

    reference_text = (
        "风卷起满地的落叶，在空中打了几个旋，又无力地落回地面。"
        "他站在街角，望着那条空荡荡的巷子，心里像被人攥住了一样。"
        "那年秋天，她也是这样离开的。没有任何告别，只留下一封信，"
        "信上只有三个字：对不起。他握着那张泛黄的纸，指节发白。"
        "眼泪最终没有掉下来，因为他已经习惯了。习惯了告别，习惯了孤独，"
        "习惯了一个人面对世界的所有恶意。可当风吹过的时候，"
        "他还是忍不住回头看了一眼。或许，她在某个角落看着他。"
        "但巷子里只有落叶，和越来越浓的暮色。"
    )

    topic = "城市里的孤独"

    print(f"  写作主题: {topic}")
    print(f"  参考文本长度: {len(reference_text)} 字")

    # 提交任务
    print("\n  提交写作任务...")
    try:
        resp = requests.post(
            f"{BASE_URL}/write",
            json={"topic": topic, "reference_text": reference_text},
            timeout=10,
        )
        if resp.status_code != 200:
            print(f"[FAIL] 任务提交失败: {resp.status_code} {resp.text}")
            return False
    except requests.exceptions.ConnectionError:
        print(f"[SKIP] 无法连接到 {BASE_URL}，请确保 FastAPI 已启动")
        return False

    data = resp.json()
    task_id = data["task_id"]
    print(f"  任务已提交: {task_id}")

    # 轮询状态
    print("\n  等待任务完成...")
    max_wait = 120  # 最多等 2 分钟
    interval = 3
    elapsed = 0

    while elapsed < max_wait:
        time.sleep(interval)
        elapsed += interval

        try:
            status_resp = requests.get(f"{BASE_URL}/status/{task_id}", timeout=10)
            status_data = status_resp.json()
            status = status_data.get("status", "unknown")
            print(f"  [{elapsed}s] 状态: {status}")

            if status == "completed":
                print("\n[PASS] 任务完成！\n")
                _print_result(task_id)
                return True
            elif status == "failed":
                print(f"\n[FAIL] 任务失败: {status_data.get('error')}")
                return False
        except Exception as e:
            print(f"  [WARN] 查询状态异常: {e}")

    print(f"\n[FAIL] 任务超时（{max_wait}s）")
    return False


def _print_result(task_id: str):
    """打印任务的完整结果。"""
    resp = requests.get(f"{BASE_URL}/result/{task_id}", timeout=10)
    result = resp.json()

    print("=" * 60)
    print("最终结果")
    print("=" * 60)

    style = result.get("style", {})
    print(f"\n📊 风格分析:")
    style_brief = style.get("style_brief", "") if isinstance(style, dict) else ""
    if style_brief:
        print(f"  风格简报: {style_brief[:120]}...")
    else:
        print(f"  主情感: {style.get('primary_emotion', 'N/A')}")
        print(f"  情感强度: {style.get('emotion_intensity', 'N/A')}/100")
        print(f"  叙事密度: {style.get('narrative_density', 'N/A')}")
        print(f"  形容词密度: {style.get('adjective_density', 'N/A')}")

    outline = result.get("outline", [])
    print(f"\n📋 大纲:")
    for item in outline:
        print(f"  第{item.get('section', '?')}节: {item.get('title', '?')}")
        for pt in item.get("key_points", []):
            print(f"    - {pt}")

    draft = result.get("draft", "")
    print(f"\n📝 正文 ({len(draft)} 字):")
    print("-" * 40)
    print(draft[:500])
    if len(draft) > 500:
        print(f"... (共 {len(draft)} 字，已截断显示)")

    review = result.get("review", {})
    print(f"\n✅ 审阅:")
    print(f"  评分: {review.get('global_score', 'N/A')}/10")
    print(f"  建议: {review.get('suggestion', 'N/A')}")
    print("=" * 60)


def test_character_models():
    """验证 CharacterProfile 和 CharacterArc 模型定义。"""
    print("=" * 60)
    print("[TEST] 测试角色数据模型")

    from app.models import CharacterProfile, CharacterArc

    # 创建角色
    c = CharacterProfile(
        name="张三",
        gender="男",
        age="28岁",
        personality=["固执", "温柔"],
        motivation="复仇",
        background="被背叛的退伍军人",
    )
    assert c.name == "张三"
    assert c.personality == ["固执", "温柔"]
    assert c.motivation == "复仇"
    assert c.key_lines == []
    assert c.id == ""

    # 创建弧线
    a = CharacterArc(
        character_id="test-id",
        starting_state="愤怒的复仇者",
        ending_state="学会放下",
        key_milestones=[{"section": 1, "event": "触发事件"}],
        current_state="愤怒的复仇者",
    )
    assert a.character_id == "test-id"
    assert len(a.key_milestones) == 1

    # WriteRequest 默认值
    from app.models import WriteRequest
    req = WriteRequest(topic="测试", reference_text="测试文本")
    assert req.character_text == ""
    assert req.characters == []

    print("[PASS] 角色数据模型测试通过\n")


def test_character_extraction():
    """测试 LLM 从自然语言提取角色。"""
    print("=" * 60)
    print("[TEST] 测试角色提取")

    if not settings.LLM_API_KEY:
        print("[SKIP] LLM_API_KEY 未设置\n")
        return

    from app.agents.character_manager import CharacterManager

    cm = CharacterManager()
    text = "张三，28岁，退伍军人。性格固执但内心柔软，口头禅是'习惯了'。他的秘密是在战场上抛弃过战友。"
    result = cm.extract_characters(text)

    assert isinstance(result, list)
    assert len(result) >= 1
    char = result[0]
    assert "name" in char
    print(f"  提取结果: {char.get('name', '?')}")
    print("[PASS] 角色提取测试通过\n")


def test_character_arc_planning():
    """测试角色弧线规划。"""
    print("=" * 60)
    print("[TEST] 测试角色弧线规划")

    if not settings.LLM_API_KEY:
        print("[SKIP] LLM_API_KEY 未设置\n")
        return

    from app.agents.character_manager import CharacterManager

    cm = CharacterManager()
    characters = [{
        "id": "test-1",
        "name": "测试角色",
        "personality": ["勇敢", "脆弱"],
        "motivation": "证明自己",
        "catchphrase": "我可以的",
    }]
    outline = [
        {
            "section": 1,
            "title": "开端",
            "key_points": ["引入角色"],
            "subsections": [{"subsection": 1, "title": "初遇", "key_points": ["首次登场"]}],
        },
        {
            "section": 2,
            "title": "转折",
            "key_points": ["关键事件"],
            "subsections": [{"subsection": 1, "title": "抉择", "key_points": ["角色做出关键选择"]}],
        },
    ]
    result = cm.plan_arcs(characters, outline)

    assert isinstance(result, list)
    if len(result) > 0:
        arc = result[0]
        assert "starting_state" in arc
        assert "ending_state" in arc
        assert "key_milestones" in arc
        assert "current_state" in arc
        print(f"  弧线: {arc.get('starting_state', '?')} → {arc.get('ending_state', '?')}")
    print("[PASS] 角色弧线规划测试通过\n")


def test_character_store():
    """测试 CharacterStore CRUD + 搜索 + 统计 + traits 表。"""
    print("=" * 60)
    print("[TEST] 测试角色库存储")

    from app.character_store import CharacterStore
    import os
    db_path = "./test_characters.db"

    # 清理旧测试文件
    if os.path.exists(db_path):
        os.remove(db_path)

    store = CharacterStore(db_path)

    # 创建
    c = store.create({
        "name": "测试张三",
        "personality": ["勇敢", "固执"],
        "strengths": ["战斗技能"],
        "weaknesses": ["不善表达"],
        "motivation": "复仇",
        "key_lines": ["这是命令"],
        "relationships": [{"target": "李四", "relation": "宿敌"}],
    })
    assert c["name"] == "测试张三"
    assert c["personality"] == ["勇敢", "固执"]
    assert c["strengths"] == ["战斗技能"]
    assert len(c["id"]) > 0
    assert "created_at" in c
    print("  CREATE: OK")

    # 查询单个
    c2 = store.get(c["id"])
    assert c2 is not None
    assert c2["name"] == "测试张三"
    assert c2["motivation"] == "复仇"
    print("  GET: OK")

    # 搜索（全文 + trait 过滤）
    results = store.list_all(search="勇敢")
    assert len(results) >= 1
    print("  SEARCH(fulltext): %d results" % len(results))

    results2 = store.list_all(trait_filter="personality:固执")
    assert len(results2) >= 1
    print("  SEARCH(trait_filter): %d results" % len(results2))

    # 查重
    dup = store.find_by_name("张三")
    assert len(dup) >= 1
    print("  FIND_BY_NAME: %d results" % len(dup))

    # 更新（含 traits 变更）
    c3 = store.update(c["id"], {
        "motivation": "证明自己",
        "personality": ["勇敢", "固执", "冷静"],
        "strengths": ["战斗技能", "领导力"],
    })
    assert c3 is not None
    assert c3["motivation"] == "证明自己"
    assert len(c3["personality"]) == 3
    assert len(c3["strengths"]) == 2
    print("  UPDATE: OK")

    # 统计
    stats = store.stats()
    assert "total" in stats
    assert stats["total"] >= 1
    assert "top_traits" in stats
    print("  STATS: total=%d, top_traits=%d" % (stats["total"], len(stats["top_traits"])))

    # 删除
    assert store.delete(c["id"]) is True
    assert store.get(c["id"]) is None
    print("  DELETE: OK")

    # 清理
    store._conn.close()
    os.remove(db_path)
    print("[PASS] 角色库存储测试通过\n")


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  多智能体协作写作系统 — 基础测试")
    print("=" * 60 + "\n")

    # Test 1: Embedding
    provider = test_embedding_provider()

    # Test 2: LLM 配置
    llm_ok = test_llm_config()

    # Test 3: 完整工作流
    workflow_ok = test_full_workflow()

    # Test 4: 角色模型
    test_character_models()

    # Test 5: 角色提取（需要 LLM）
    test_character_extraction()

    # Test 6: 角色弧线规划（需要 LLM）
    test_character_arc_planning()

    # Test 7: 角色库存储
    test_character_store()

    # Summary
    print("\n" + "=" * 60)
    print("  测试总结")
    print("=" * 60)
    print(f"  Embedding 提供商: {provider.model_name} (维度: {provider.dimension})")
    print(f"  LLM 模型: {settings.LLM_MODEL}")
    print(f"  LLM 配置: {'通过' if llm_ok else '未测试/失败'}")
    print(f"  完整流程: {'通过' if workflow_ok else '未测试/失败'}")
    print("=" * 60 + "\n")
