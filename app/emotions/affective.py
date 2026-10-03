"""Persistent affective chemistry, deliberately deterministic and per-chat."""
from __future__ import annotations
import json, math
from datetime import datetime, timezone
from dataclasses import dataclass

REGULATOR_BASELINES = {
 "valence":.54,"arousal":.30,"energy":.62,"reward_sensitivity":.55,"stress":.15,"social_safety":.72,
 "attachment":.72,"social_need":.45,"novelty_drive":.55,"threat":.08,"frustration":.05,"self_worth":.62,
 "grief_load":.04,"resentment":.03,"vulnerability":.25,"inhibition":.55,
}
REGULATORS=tuple(REGULATOR_BASELINES)
def clamp(v): return max(0.,min(1.,float(v)))
@dataclass(frozen=True)
class AffectiveChemistry:
 values_map: dict
 @classmethod
 def baseline(cls): return cls(dict(REGULATOR_BASELINES))
 def values(self): return dict(self.values_map)
 def with_delta(self, delta): return AffectiveChemistry({k:clamp(self.values_map[k]+delta.get(k,0)) for k in REGULATORS})

class AffectiveEngine:
 def __init__(self, db): self.db=db
 @staticmethod
 def _stamp(now): return now.astimezone(timezone.utc).isoformat(timespec='microseconds')
 async def get(self, chat_id, *, now=None, sleeping=False):
  now=now or datetime.now(timezone.utc); now=now.astimezone(timezone.utc)
  row=await self.db.fetchone('SELECT * FROM affective_states WHERE chat_id=?',(chat_id,))
  if not row:
   values=AffectiveChemistry.baseline().values(); stamp=self._stamp(now)
   await self.db.execute('INSERT INTO affective_states(chat_id,regulators_json,last_advanced_at,updated_at) VALUES(?,?,?,?)',(chat_id,json.dumps(values),stamp,stamp)); return AffectiveChemistry(values)
  before=AffectiveChemistry(json.loads(row['regulators_json']))
  then=datetime.fromisoformat(row['last_advanced_at']).astimezone(timezone.utc); hours=max(0,(now-then).total_seconds()/3600)
  if not hours:return before
  values=before.values()
  for name,base in REGULATOR_BASELINES.items():
   if name == 'attachment': continue
   half=96 if name in {'attachment','resentment','grief_load'} else 18
   values[name]=base+(values[name]-base)*math.exp(-math.log(2)*hours/half)
  from app.emotions.relationship import RelationshipBondManager
  bond=await RelationshipBondManager(self.db).get(chat_id)
  values['attachment']=clamp(values['attachment']+(bond.attachment_target-values['attachment'])*(1-math.exp(-hours/96)))
  values['energy']=clamp(values['energy']+((.85 if sleeping else .55)-values['energy'])*(1-math.exp(-hours/3)))
  values['social_need']=clamp(values['social_need']+min(.12,hours*.005))
  stamp=self._stamp(now); await self.db.execute('UPDATE affective_states SET regulators_json=?,last_advanced_at=?,updated_at=? WHERE chat_id=?',(json.dumps(values),stamp,stamp,chat_id)); return AffectiveChemistry(values)
 async def apply_appraisal(self, chat_id, appraisal, *, now=None, test_mode=False):
  state=await self.get(chat_id,now=now); delta=appraisal_to_delta(appraisal)
  updated=state.with_delta(delta); stamp=self._stamp((now or datetime.now(timezone.utc)).astimezone(timezone.utc))
  await self.db.execute('UPDATE affective_states SET regulators_json=?,updated_at=? WHERE chat_id=?',(json.dumps(updated.values()),stamp,chat_id))
  from app.emotions.profile import calculate, behavior
  previous=await self.db.fetchone('SELECT emotions_json FROM affective_profiles WHERE chat_id=?',(chat_id,))
  from app.emotions.relationship import RelationshipBondManager
  _,bond=await RelationshipBondManager(self.db).apply(chat_id,appraisal,test_mode=test_mode)
  emotions=calculate(updated, json.loads(previous['emotions_json']) if previous else None, signals=appraisal)
  await self.db.execute('INSERT INTO affective_profiles(chat_id,emotions_json,last_updated_at) VALUES(?,?,?) ON CONFLICT(chat_id) DO UPDATE SET emotions_json=excluded.emotions_json,last_updated_at=excluded.last_updated_at',(chat_id,json.dumps(emotions.values()),stamp))
  return state,delta,updated,emotions,behavior(updated,emotions,bond)
 async def get_emotions(self,chat_id):
  row=await self.db.fetchone('SELECT emotions_json,last_updated_at FROM affective_profiles WHERE chat_id=?',(chat_id,))
  from app.emotions.profile import EmotionProfile,calculate
  if not row: return calculate(await self.get(chat_id))
  previous=json.loads(row['emotions_json'])
  now=datetime.now(timezone.utc)
  elapsed=max(0.,(now-datetime.fromisoformat(row['last_updated_at']).astimezone(timezone.utc)).total_seconds()/3600)
  if elapsed < 1/60: return EmotionProfile(previous)
  target=calculate(await self.get(chat_id)).values()
  values={name:clamp(value+(target[name]-value)*(1-math.exp(-elapsed/(20 if name in {'hurt','resentment','grief','closeness','trust'} else 3)))) for name,value in previous.items()}
  await self.db.execute('UPDATE affective_profiles SET emotions_json=?,last_updated_at=? WHERE chat_id=?',(json.dumps(values),self._stamp(now),chat_id))
  return EmotionProfile(values)
 async def get_behavior(self,chat_id):
  from app.emotions.profile import behavior
  from app.emotions.relationship import RelationshipBondManager
  state=await self.get(chat_id)
  bond=await RelationshipBondManager(self.db).get(chat_id)
  row=await self.db.fetchone("SELECT (julianday('now')-julianday(last_meaningful_interaction_at))*24 AS hours FROM conversation_lifecycle WHERE chat_id=?",(chat_id,))
  absence=max(0.,row['hours'] or 0.) if row else 0.
  return behavior(state,await self.get_emotions(chat_id),bond,absence_hours=absence)

def appraisal_to_delta(appraisal):
 a=appraisal.values() if hasattr(appraisal,'values') else {k:v for k,v in appraisal.items() if v is not None}; scale=a.get('intensity',.35); val=a.get('valence',0)*scale
 d={'valence':val*.08,'reward_sensitivity':max(0,val)*.025,'social_safety':a.get('warmth',0)*.05+a.get('closeness',0)*.04-a.get('rejection',0)*.08-a.get('dismissal',0)*.04+a.get('repair_attempt',0)*.05,'attachment':(a.get('warmth',0)+a.get('closeness',0))*.01,'resentment':a.get('rejection',0)*.05+a.get('other_blame',0)*.035+a.get('betrayal',0)*.04,'vulnerability':a.get('rejection',0)*.05+a.get('replacement_threat',0)*.04,'grief_load':a.get('loss',0)*.06,'energy':-a.get('loss',0)*.025,'frustration':(a.get('frustration',0)+a.get('other_blame',0)*.5+a.get('dismissal',0)*.5)*.06-a.get('repair_attempt',0)*.03,'stress':(a.get('threat',0)+a.get('uncertainty',0)+a.get('frustration',0)*.4+a.get('replacement_threat',0)*.5)*.06-a.get('relief',0)*.06,'threat':a.get('threat',0)*.08+a.get('uncertainty',0)*.02+a.get('replacement_threat',0)*.06-a.get('relief',0)*.08-a.get('repair_attempt',0)*.04,'arousal':(a.get('threat',0)+a.get('novelty',0)+a.get('humor',0)*.3+a.get('replacement_threat',0)*.5)*.06,'self_worth':a.get('achievement',0)*.03-a.get('self_blame',0)*.03,'novelty_drive':a.get('novelty',0)*.03}
 caps={'attachment':.02,'self_worth':.03,'resentment':.05,'grief_load':.06}
 return {k:max(-caps.get(k,.08),min(caps.get(k,.08),v)) for k,v in d.items() if v}
