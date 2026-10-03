from datetime import datetime, timedelta, timezone
import pytest
from app.database.db import Database
from app.emotions.affective import AffectiveEngine, REGULATOR_BASELINES

@pytest.mark.asyncio
async def test_affective_chemistry_persists_and_sleep_restores_energy(tmp_path):
 db=Database(f"sqlite:///{tmp_path/'a.sqlite'}"); await db.connect(); engine=AffectiveEngine(db); start=datetime(2026,1,1,tzinfo=timezone.utc)
 assert (await engine.get(1,now=start)).values()==REGULATOR_BASELINES
 state=await engine.get(1,now=start+timedelta(hours=6),sleeping=True)
 assert state.values()['energy'] > .62
 assert (await engine.get(1,now=start+timedelta(hours=6),sleeping=True)).values()==state.values()
 await db.close()
