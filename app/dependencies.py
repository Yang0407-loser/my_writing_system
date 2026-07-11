"""共享依赖：所有 router 共用的单例。"""

from .blackboard import Blackboard
from .character_store import CharacterStore

bb = Blackboard()
char_store = CharacterStore()
