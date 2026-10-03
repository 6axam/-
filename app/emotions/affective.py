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
   half=96 if name in {'attachment','resentment','grief_load'} else 18
   values[name]=base+(values[name]-base)*math.exp(-math.log(2)*hours/half)
  values['energy']=clamp(values['energy']+((.85 if sleeping else .55)-values['energy'])*(1-math.exp(-hours/3)))
  values['social_need']=clamp(values['social_need']+min(.12,hours*.005))
  stamp=self._stamp(now); await self.db.execute('UPDATE affective_states SET regulators_json=?,last_advanced_at=?,updated_at=? WHERE chat_id=?',(json.dumps(values),stamp,stamp,chat_id)); return AffectiveChemistry(values)
