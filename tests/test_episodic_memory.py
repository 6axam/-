import json
import pytest
from app.database.db import Database
from app.emotions.model import EmotionalState
from app.episodic_memory import EpisodicMemoryManager
from app.llm.schemas import MemoryEpisode

@pytest.mark.asyncio
async def test_episode_snapshot_open_loop_and_safe_resolution(tmp_path):
 db=Database(f"sqlite:///{tmp_path/'e.sqlite'}"); await db.connect(); m=EpisodicMemoryManager(db); state=EmotionalState(warmth=.8)
 ident=await m.apply(1,MemoryEpisode(kind='open_loop',summary='Надо проверить голос',importance=.7,unresolved=True),state)
 row=await db.fetchone('SELECT * FROM episodic_memories WHERE id=?',(ident,)); assert row['unresolved'] and json.loads(row['emotion_snapshot'])['warmth']==.8
 await m.apply(1,MemoryEpisode(kind='shared_event',summary='неважно',importance=.7,resolve_episode_ids=[999]),state,exposed_ids={ident})
 assert (await db.fetchone('SELECT unresolved FROM episodic_memories WHERE id=?',(ident,)))['unresolved'] == 1
 await m.apply(1,MemoryEpisode(kind='shared_event',summary='готово',importance=.7,resolve_episode_ids=[ident]),state,exposed_ids={ident})
 assert (await db.fetchone('SELECT unresolved FROM episodic_memories WHERE id=?',(ident,)))['unresolved'] == 0
 await db.close()

@pytest.mark.asyncio
async def test_superseded_is_not_retrieved_and_chats_are_isolated(tmp_path):
 db=Database(f"sqlite:///{tmp_path/'e.sqlite'}"); await db.connect(); m=EpisodicMemoryManager(db); s=EmotionalState()
 old=await m.apply(1,MemoryEpisode(kind='user_update',summary='используем byteplus',importance=.8),s)
 await m.apply(1,MemoryEpisode(kind='user_update',summary='используем openrouter',importance=.8,supersede_episode_id=old),s,exposed_ids={old})
 await m.apply(2,MemoryEpisode(kind='shared_event',summary='используем byteplus',importance=.8),s)
 assert all(r['id'] != old for r in await m.relevant(1,'byteplus openrouter'))
 assert len(await m.relevant(2,'byteplus')) == 1
 await db.close()
