import json
from app.emotions.history import AffectiveHistory
def test_history_appends_unicode(tmp_path):
 path=tmp_path/'data'/'history.jsonl'; h=AffectiveHistory(path); before={'valence':.5}; after={'valence':.4}; emotions={'hurt':.1}
 h.append(chat_id=1,message_id=2,generation_id='g',user_message='бля',before=before,after=after,emotions_before=emotions,emotions_after={'hurt':.3},behavior={'patience':.2},appraisal={'valence':-.5})
 h.append(chat_id=1,message_id=3,generation_id='h',user_message='ок',before=after,after=after,emotions_before=emotions,emotions_after=emotions,behavior={},appraisal={})
 rows=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
 assert len(rows)==2 and rows[0]['user_message']=='бля' and rows[0]['regulator_delta']['valence']==-.1
