import json
import sys
from app.emotions.history import AffectiveHistory
from app.tools.affective_history import main
def test_history_appends_unicode(tmp_path):
 path=tmp_path/'data'/'history.jsonl'; h=AffectiveHistory(path); before={'valence':.5}; after={'valence':.4}; emotions={'hurt':.1}
 h.append(chat_id=1,message_id=2,generation_id='g',user_message='бля',before=before,after=after,emotions_before=emotions,emotions_after={'hurt':.3},behavior={'patience':.2},appraisal={'valence':-.5})
 h.append(chat_id=1,message_id=3,generation_id='h',user_message='ок',before=after,after=after,emotions_before=emotions,emotions_after=emotions,behavior={},appraisal={})
 rows=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
 assert len(rows)==2 and rows[0]['user_message']=='бля' and rows[0]['regulator_delta']['valence']==-.1

def test_history_cli_shows_relationship_transitions(tmp_path, monkeypatch, capsys):
 path=tmp_path/'history.jsonl'
 AffectiveHistory(path).append(chat_id=1,message_id=2,generation_id='g',user_message='event',
  before={'valence':.5},after={'valence':.4},emotions_before={'hurt':.1},emotions_after={'hurt':.3},
  behavior={'protest_drive':.6,'contact_drive':.5},appraisal={'rejection':1},
  current_expression={'impatience':.7,'protest_tendency':.6,'repair_tendency':.1,'profanity_tendency':.5,'reflective_control':.3},
  relationship_before={'bond_strength':.9,'relational_trust':.8,'rupture_load':.1,'love_strength':.9},
  relationship_after={'bond_strength':.9,'relational_trust':.78,'rupture_load':.13,'love_strength':.89})
 monkeypatch.setattr(sys,'argv',['affective_history','--last','1','--path',str(path)])
 main()
 output=capsys.readouterr().out
 assert 'RELATIONSHIP' in output and 'bond_strength' in output and 'relational_trust' in output
 assert 'protest_drive=0.60' in output
 assert 'CURRENT EXPRESSION' in output and 'protest_tendency' in output and 'repair_tendency' in output
