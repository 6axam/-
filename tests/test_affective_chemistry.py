from datetime import datetime, timedelta, timezone
import pytest
from app.database.db import Database
from app.emotions.affective import AffectiveEngine, REGULATOR_BASELINES
from app.emotions.relationship import RelationshipBondManager, RelationshipBondState

@pytest.mark.asyncio
async def test_affective_chemistry_persists_and_sleep_restores_energy(tmp_path):
 db=Database(f"sqlite:///{tmp_path/'a.sqlite'}"); await db.connect(); engine=AffectiveEngine(db); start=datetime(2026,1,1,tzinfo=timezone.utc)
 assert (await engine.get(1,now=start)).values()==REGULATOR_BASELINES
 state=await engine.get(1,now=start+timedelta(hours=6),sleeping=True)
 assert state.values()['energy'] > .62
 assert (await engine.get(1,now=start+timedelta(hours=6),sleeping=True)).values()==state.values()
 await db.close()

@pytest.mark.asyncio
async def test_attachment_drifts_to_learned_bond_not_generic_baseline(tmp_path):
 db=Database(f"sqlite:///{tmp_path/'learned.sqlite'}"); await db.connect()
 engine=AffectiveEngine(db); start=datetime(2026,1,1,tzinfo=timezone.utc)
 await engine.get(1,now=start)
 bonds=RelationshipBondManager(db)
 await bonds._save(1,RelationshipBondState(.95,.9,.9,.9,.05,.9,.9))
 high=await engine.get(1,now=start+timedelta(days=8))
 assert high.values()['attachment'] > REGULATOR_BASELINES['attachment']
 await bonds._save(1,RelationshipBondState(.12,.15,.3,.2,.7,.2,.15))
 low=await engine.get(1,now=start+timedelta(days=16))
 assert low.values()['attachment'] < high.values()['attachment']
 await db.close()
