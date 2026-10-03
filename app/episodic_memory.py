import json, logging
from app.memory.manager import _lexical_overlap
log = logging.getLogger(__name__)

class EpisodicMemoryManager:
    def __init__(self, db): self.db = db
    @staticmethod
    def build_affective_snapshot(chemistry, emotions, behavior):
        """Return the canonical immutable V2 snapshot stored with an episode."""
        chemistry_values=chemistry.values() if hasattr(chemistry,'values') else dict(chemistry)
        emotion_values=emotions.values() if hasattr(emotions,'values') else dict(emotions)
        behavior_values=behavior.values() if hasattr(behavior,'values') else dict(behavior)
        salient=dict(sorted(emotion_values.items(),key=lambda item:item[1],reverse=True)[:10])
        return {'version':2,'chemistry':chemistry_values,'salient_emotions':salient,'behavior':behavior_values}
    @staticmethod
    def _snapshot(value):
        try:
            data=json.loads(value or '{}')
        except (TypeError,json.JSONDecodeError): return {}
        return data if isinstance(data,dict) else {}
    @staticmethod
    def _resonance(current, snapshot):
        chemistry=snapshot.get('chemistry',snapshot)
        current=current.get('chemistry',current) if isinstance(current,dict) else {}
        keys=('valence','arousal','social_safety','attachment','stress','grief_load','resentment','vulnerability')
        shared=[key for key in keys if key in chemistry and key in current]
        if not shared:return 0
        return max(0.,1-sum(abs(current[key]-chemistry[key]) for key in shared)/len(shared))
    @staticmethod
    def _conflict_weight(snapshot):
        emotions=snapshot.get('salient_emotions',{})
        behavior=snapshot.get('behavior',{})
        values=[emotions.get(key,0) for key in ('hurt','resentment','anger','defensiveness')]
        values += [behavior.get(key,0) for key in ('hostility','protest_drive','rumination_drive')]
        return max(values,default=0)
    async def relevant(self, chat_id, query, limit=6, current_affective=None, conflict_activation=0):
        rows = await self.db.fetchall("SELECT * FROM episodic_memories WHERE chat_id=? AND status='active' ORDER BY updated_at DESC,id DESC LIMIT 100", (chat_id,))
        ranked=[]
        for r in rows:
            relevance=_lexical_overlap(query, r['summary']+' '+r['reflection'])
            # Importance/confidence are tie-breakers, never a substitute for
            # topical relevance. Open loops get only a small lower threshold.
            threshold=.035 if r['unresolved'] else .08
            if relevance < threshold: continue
            # Resonance only breaks close topical ties; it cannot pass the floor.
            snapshot=self._snapshot(r['emotion_snapshot'])
            resonance=min(.05,self._resonance(current_affective or {},snapshot)*.05)
            conflict_bonus=min(.14, conflict_activation*self._conflict_weight(snapshot)*.14)
            score=relevance + r['importance']*.08 + r['confidence']*.05 + r['unresolved']*.05 + resonance + conflict_bonus
            ranked.append((score,r))
        return [r for _,r in sorted(ranked,key=lambda v:(v[0],v[1]['id']),reverse=True)[:limit]]
    async def open_loops(self, chat_id, query="", limit=3):
        rows = await self.db.fetchall("SELECT *, (julianday('now')-julianday(created_at))/30.0 AS age_days FROM episodic_memories WHERE chat_id=? AND status='active' AND unresolved=1 ORDER BY updated_at DESC,id DESC LIMIT 48", (chat_id,))
        ranked=[]
        for row in rows:
            relevance=_lexical_overlap(query, row['summary']+' '+row['reflection'])
            age=max(0., float(row['age_days'] or 0))
            score=row['importance']*.45 + row['confidence']*.25 + min(.12, relevance*.12) - min(.28, age*.008)
            ranked.append((score,row))
        return [row for _,row in sorted(ranked,key=lambda pair:(pair[0],pair[1]['id']),reverse=True)[:limit]]
    async def apply(self, chat_id, episode, state, *, affective_snapshot=None, resolve_episode_ids=(), generation_id=None, message_id=None, exposed_ids=frozenset()):
        for ident in resolve_episode_ids:
            if ident in exposed_ids:
                await self.db.execute("UPDATE episodic_memories SET unresolved=0,resolved_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=? AND chat_id=? AND status='active'",(ident,chat_id)); log.info("episodic_memory_resolved chat_id=%s id=%s",chat_id,ident)
            else: log.debug("episodic_memory_invalid_resolve chat_id=%s id=%s",chat_id,ident)
        if not episode or (episode.importance < .35 and not episode.unresolved): return None
        snap=json.dumps(affective_snapshot or state.values(),sort_keys=True,separators=(',',':'))
        result=await self.db.execute("INSERT INTO episodic_memories(chat_id,kind,summary,reflection,importance,confidence,unresolved,emotion_snapshot,source_generation_id,source_message_id) VALUES(?,?,?,?,?,?,?,?,?,?)",(chat_id,episode.kind,episode.summary.strip(),episode.reflection.strip(),episode.importance,episode.confidence,int(episode.unresolved),snap,generation_id,message_id))
        if episode.supersede_episode_id in exposed_ids:
            await self.db.execute("UPDATE episodic_memories SET status='superseded',superseded_by=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND chat_id=? AND status='active'",(result.lastrowid,episode.supersede_episode_id,chat_id)); log.info("episodic_memory_superseded old_id=%s new_id=%s",episode.supersede_episode_id,result.lastrowid)
        log.info("episodic_memory_created chat_id=%s id=%s kind=%s importance=%.2f unresolved=%s",chat_id,result.lastrowid,episode.kind,episode.importance,episode.unresolved)
        return result.lastrowid
