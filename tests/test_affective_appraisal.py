import pytest
from app.database.db import Database
from app.emotions.affective import AffectiveEngine, appraisal_to_delta
from app.llm.schemas import AffectiveAppraisal, LLMResponse

def test_appraisal_schema_and_caps():
 assert LLMResponse().affective_appraisal.values()=={}
 with pytest.raises(Exception): AffectiveAppraisal(valence=2)
 d=appraisal_to_delta(AffectiveAppraisal(valence=-1,intensity=1,rejection=1,other_blame=1,loss=1))
 assert d['resentment'] <= .05 and d['grief_load'] <= .06 and all(abs(v)<=.08 for v in d.values())

@pytest.mark.asyncio
async def test_appraisal_persists_profile_and_accumulates(tmp_path):
 db=Database(f"sqlite:///{tmp_path/'a.sqlite'}"); await db.connect(); engine=AffectiveEngine(db)
 rejection=AffectiveAppraisal(valence=-1,intensity=1,rejection=1,other_blame=1,social_relevance=1)
 _,_,first,first_profile,_=await engine.apply_appraisal(1,rejection)
 _,_,second,second_profile,_=await engine.apply_appraisal(1,rejection)
 assert second.values()['resentment'] > first.values()['resentment']
 assert second_profile.values()['hurt'] >= first_profile.values()['hurt']
 restarted= AffectiveEngine(db)
 assert (await restarted.get(1)).values()['resentment']==pytest.approx(second.values()['resentment'])
 assert (await restarted.get_emotions(1)).values()==second_profile.values()
 await db.close()
