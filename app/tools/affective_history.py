import argparse,json
from pathlib import Path
def main():
 p=argparse.ArgumentParser(); p.add_argument('--last',type=int,default=20); p.add_argument('--chat-id',type=int); p.add_argument('--event-type'); p.add_argument('--path',default='data/affective_history.jsonl'); a=p.parse_args(); path=Path(a.path)
 if not path.exists(): return
 rows=[]
 for line in path.read_text(encoding='utf-8').splitlines():
  try: row=json.loads(line)
  except json.JSONDecodeError: continue
  if (a.chat_id is None or row.get('chat_id')==a.chat_id) and (a.event_type is None or row.get('event_type')==a.event_type): rows.append(row)
 for row in rows[-a.last:]:
  print(f"{row['timestamp']}  {row['event_type'].upper()}");
  if row.get('user_message') is not None: print(json.dumps(row['user_message'],ensure_ascii=False))
  for label,before,delta in [('REGULATORS',row.get('regulators_before',{}),row.get('regulator_delta',{})),('EMOTIONS',row.get('emotions_before',{}),row.get('emotion_delta',{}))]:
   print(label)
   for k,v in delta.items(): print(f"{k:18} {before[k]:.2f} -> {before[k]+v:.2f} ({v:+.2f})")
  if 'relationship_after' in row:
   print('RELATIONSHIP')
   before=row.get('relationship_before',{}); after=row['relationship_after']
   for name in ('bond_strength','relational_trust','rupture_load','love_strength','relationship_security','care_investment','familiarity'):
    if name in before and name in after:
     print(f"{name:18} {before[name]:.2f} -> {after[name]:.2f} ({after[name]-before[name]:+.2f})")
  print('BEHAVIOR'); print(' '.join(f'{k}={v:.2f}' for k,v in row.get('behavior_after',{}).items())); print()
if __name__=='__main__': main()
