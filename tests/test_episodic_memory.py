import json
import pytest
from app.actions.models import Action, ActionType
from app.actions.queue import ActionQueue
from app.conversation.context import ContextBuilder
from app.conversation.manager import ConversationManager
from app.database.db import Database
from app.emotions.affective import AffectiveEngine
from app.emotions.engine import EmotionalEngine
from app.emotions.history import AffectiveHistory
from app.emotions.model import EmotionalState
from app.episodic_memory import EpisodicMemoryManager
from app.llm.schemas import AffectiveAppraisal, LLMResponse, MemoryEpisode

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
async def test_conversation_lifecycle_stores_post_event_v2_affective_snapshot(tmp_path, monkeypatch):
 class Provider:
  calls=0
  async def generate(self, _request):
   self.calls += 1
   return LLMResponse(
    actions=[Action(type=ActionType.text,text='ok')],
    affective_appraisal=AffectiveAppraisal(valence=-1,intensity=1,rejection=1,other_blame=1,social_relevance=1),
    memory_episode=MemoryEpisode(kind='relationship',summary='важный разговор',importance=.8),
   )
 class Executor:
  async def execute(self, _item): pass

 monkeypatch.setattr(AffectiveHistory,'append',lambda *_args,**_kwargs:None)
 db=Database(f"sqlite:///{tmp_path/'lifecycle.sqlite'}"); await db.connect()
 episodic=EpisodicMemoryManager(db); affective=AffectiveEngine(db); legacy=EmotionalEngine(db)
 baseline_chemistry=await affective.get(7)
 baseline_profile=await affective.get_emotions(7)
 baseline_snapshot=episodic.build_affective_snapshot(baseline_chemistry,baseline_profile,await affective.get_behavior(7))
 provider=Provider()
 manager=ConversationManager(provider,ContextBuilder(db,emotion_engine=legacy,episodic_memory=episodic),ActionQueue(Executor()),emotion_engine=legacy,episodic_memory=episodic)
 manager.affective_engine=affective

 await manager.handle_turn(1,7,'ты меня задела',target_message_id=41)

 row=await db.fetchone('SELECT * FROM episodic_memories WHERE chat_id=?',(7,))
 snapshot=json.loads(row['emotion_snapshot'])
 chemistry=json.loads((await db.fetchone('SELECT regulators_json FROM affective_states WHERE chat_id=?',(7,)))['regulators_json'])
 profile=json.loads((await db.fetchone('SELECT emotions_json FROM affective_profiles WHERE chat_id=?',(7,)))['emotions_json'])
 assert provider.calls == 1
 assert snapshot['version'] == 2
 assert snapshot['chemistry'] == chemistry
 assert any(snapshot['salient_emotions'].get(name)==value for name,value in profile.items())
 assert snapshot != baseline_snapshot
 assert row['source_message_id'] == 41
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
