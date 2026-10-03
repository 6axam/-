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
 await m.apply(1,None,state,resolve_episode_ids=[999],exposed_ids={ident})
 assert (await db.fetchone('SELECT unresolved FROM episodic_memories WHERE id=?',(ident,)))['unresolved'] == 1
 await m.apply(1,None,state,resolve_episode_ids=[ident],exposed_ids={ident})
 assert (await db.fetchone('SELECT unresolved FROM episodic_memories WHERE id=?',(ident,)))['unresolved'] == 0
 await db.close()

@pytest.mark.asyncio
async def test_unrelated_important_episodes_do_not_bypass_relevance(tmp_path):
 db=Database(f"sqlite:///{tmp_path/'e.sqlite'}"); await db.connect(); m=EpisodicMemoryManager(db); s=EmotionalState()
 for index in range(10): await m.apply(1,MemoryEpisode(kind='shared_event',summary=f'важный проект {index}',importance=1,confidence=1),s)
 assert await m.relevant(1,'привет') == []
 await db.close()

@pytest.mark.asyncio
async def test_v2_snapshot_is_immutable_and_resonance_cannot_bypass_semantics(tmp_path):
 db=Database(f"sqlite:///{tmp_path/'e.sqlite'}"); await db.connect(); m=EpisodicMemoryManager(db); s=EmotionalState()
 snap={'version':2,'chemistry':{'valence':.1,'arousal':.9,'social_safety':.1,'attachment':.9,'stress':.9,'grief_load':.8,'resentment':.8,'vulnerability':.9},'salient_emotions':{'hurt':.9},'behavior':{'avoidance':.8}}
 ident=await m.apply(1,MemoryEpisode(kind='shared_event',summary='настройка голоса',importance=.8),s,affective_snapshot=snap)
 row=await db.fetchone('SELECT emotion_snapshot FROM episodic_memories WHERE id=?',(ident,)); assert json.loads(row['emotion_snapshot'])==snap
 assert await m.relevant(1,'привет',current_affective=snap['chemistry']) == []
 assert (await m.relevant(1,'голоса',current_affective=snap['chemistry']))[0]['id']==ident
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
