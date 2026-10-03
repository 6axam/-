"""Local append-only affective debug history."""
import json, logging
from pathlib import Path
log=logging.getLogger(__name__)
def changed(before, after, epsilon=1e-6):
 return {key: round(after[key]-before[key],8) for key in after if abs(after[key]-before[key])>epsilon}
class AffectiveHistory:
 def __init__(self,path='data/affective_history.jsonl'): self.path=Path(path)
 def append(self, *, chat_id, message_id, generation_id, user_message, before, after, emotions_before, emotions_after, behavior, appraisal, event_type='message', source='llm_appraisal'):
  record={'timestamp':__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),'chat_id':chat_id,'message_id':message_id,'generation_id':generation_id,'event_type':event_type,'user_message':user_message,'regulators_before':before,'regulator_delta':changed(before,after),'regulators_after':after,'emotions_before':emotions_before,'emotion_delta':changed(emotions_before,emotions_after),'emotions_after':emotions_after,'behavior_after':behavior,'appraisal':appraisal,'source':source}
  try:
   self.path.parent.mkdir(parents=True,exist_ok=True)
   with self.path.open('a',encoding='utf-8') as file: file.write(json.dumps(record,ensure_ascii=False,separators=(',',':'))+'\n')
  except OSError: log.warning('affective_history_write_failed',exc_info=True)
