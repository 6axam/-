import asyncio, logging
from datetime import datetime, timedelta, timezone
from app.llm.schemas import LLMRequest

log = logging.getLogger(__name__)

class DailyLifeScheduler:
    def __init__(self, db, provider, presence, settings): self.db,self.provider,self.presence,self.settings=db,provider,presence,settings
    async def run_once(self):
        rows=await self.db.fetchall("SELECT chat_id,max(user_id) user_id FROM messages WHERE sender='user' GROUP BY chat_id")
        for row in rows:
            state=await self.presence.state(row['chat_id'])
            # College/travel is already the regular daytime background; do
            # not layer invented mundane events on top of it.
            if state['availability']=='sleep' or state['event'] or state['phase']=='college': continue
            recent=await self.db.fetchone("SELECT 1 FROM daily_events WHERE chat_id=? AND julianday('now')-julianday(created_at)<0.125",(row['chat_id'],))
            if recent: continue
            context=f"CURRENT DAILY STATE\navailability={state['availability']}; timezone={self.presence.timezone_name}. Create only a present mundane event, not a retrospective excuse."
            decision=await self.provider.decide_daily_life(LLMRequest(system="",context=context,user_turn=""))
            if not decision.create_event or not decision.title: continue
            end=(datetime.now(timezone.utc)+timedelta(minutes=decision.duration_minutes)).strftime('%Y-%m-%d %H:%M:%S')
            await self.db.execute("INSERT INTO daily_events(chat_id,title,availability,starts_at,ends_at,mentionable) VALUES(?,?,?,CURRENT_TIMESTAMP,?,?)",(row['chat_id'],decision.title,decision.availability,end,int(decision.mentionable)))
            log.info("daily_event_created chat_id=%s title=%s availability=%s",row['chat_id'],decision.title,decision.availability)
    async def run(self):
        while True:
            try: await self.run_once()
            except Exception: log.exception('daily_life_cycle_failed')
            await asyncio.sleep(self.settings.daily_life_check_interval_minutes*60)
