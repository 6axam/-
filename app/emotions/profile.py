"""Derived emotional activations and behaviour from affective chemistry."""
from dataclasses import dataclass
from app.emotions.affective import clamp

EMOTION_NAMES = ('joy','contentment','excitement','amusement','relief','hope','gratitude','pride','affection','tenderness','trust','closeness','longing','loneliness','curiosity','interest','anticipation','surprise','boredom','sadness','melancholy','grief','disappointment','apathy','depressive_tone','irritation','frustration','anger','hurt','resentment','jealousy','envy','disgust','anxiety','fear','shame','guilt','embarrassment','nostalgia','overwhelm','defensiveness','regret')
FAST={'surprise','amusement','excitement','irritation'}; SLOW={'melancholy','grief','resentment','closeness','trust','depressive_tone'}
@dataclass(frozen=True)
class EmotionProfile:
 values_map: dict
 def values(self): return dict(self.values_map)
@dataclass(frozen=True)
class AffectiveBehaviorProfile:
 response_energy:float; verbal_fluency:float; warmth_expression:float; openness:float; playfulness:float; patience:float; initiative_drive:float; social_seeking:float; avoidance:float; irritability:float; expression_intensity:float
 def values(self): return self.__dict__.copy()
def _mix(previous, target, name):
 rate=.18 if name in SLOW else (.62 if name in FAST else .36)
 return clamp(previous.get(name,0)* (1-rate) + target*rate)
def calculate(chemistry, previous=None):
 c=chemistry.values(); first=previous is None; p=(previous or {}).copy(); neg=1-c['valence']; a=c['arousal']; attachment=c['attachment']; hurt=clamp((neg+c['vulnerability']+attachment+c['resentment'])/4)
 targets={'joy':c['valence']*c['reward_sensitivity'],'contentment':c['valence']*(1-a*.3),'excitement':c['valence']*a,'amusement':c['valence']*c['reward_sensitivity']*.6,'relief':max(0,p.get('anxiety',0)-c['threat'])*.8,'hope':c['valence']*c['novelty_drive'],'gratitude':c['valence']*c['social_safety']*.5,'pride':c['self_worth']*c['valence'],'affection':attachment*c['social_safety']*(.55+.45*c['valence']),'tenderness':attachment*c['vulnerability']*.7,'trust':c['social_safety']*attachment,'closeness':attachment*c['social_safety'],'longing':attachment*c['social_need'],'loneliness':c['social_need']*neg,'curiosity':c['novelty_drive']*c['reward_sensitivity'],'interest':c['novelty_drive']*c['valence'],'anticipation':c['novelty_drive']*a,'surprise':a*c['novelty_drive']*.5,'boredom':(1-c['novelty_drive'])*(1-a),'sadness':neg*c['grief_load'],'melancholy':neg*c['grief_load']*.7,'grief':c['grief_load'],'disappointment':neg*c['reward_sensitivity'],'apathy':(1-c['energy'])*(1-c['reward_sensitivity'])*(1-a),'depressive_tone':(neg+(1-c['energy'])+(1-c['reward_sensitivity'])+c['grief_load']+c['stress'])/5,'irritation':c['frustration']*a,'frustration':c['frustration'],'anger':c['frustration']*a*c['threat'],'hurt':hurt,'resentment':c['resentment'],'jealousy':attachment*c['threat']*.4,'envy':c['threat']*(1-c['self_worth'])*.3,'disgust':c['threat']*c['frustration']*.4,'anxiety':c['stress']*c['threat']*max(.4,a),'fear':c['threat']*a,'shame':(1-c['self_worth'])*c['vulnerability']*.4,'guilt':c['frustration']*(1-c['self_worth'])*.2,'embarrassment':c['vulnerability']*(1-c['social_safety'])*.3,'nostalgia':attachment*c['grief_load']*.3,'overwhelm':c['stress']*a,'defensiveness':c['threat']*(1-c['social_safety']),'regret':p.get('anger',0)*(1-a)*attachment*(1-c['inhibition'])}
 values={n:(clamp(targets.get(n,0)) if first else _mix(p,targets.get(n,0),n)) for n in EMOTION_NAMES}; return EmotionProfile(values)
def behavior(chemistry, emotions):
 c=chemistry.values(); e=emotions.values(); withdrawn=e['hurt']*.45+(1-c['energy'])*.3+(1-c['social_safety'])*.25
 return AffectiveBehaviorProfile(clamp(c['energy']*(1-e['apathy']*.5)),clamp(.65+c['energy']*.25-e['apathy']*.5-e['hurt']*.3),clamp(e['affection']-e['hurt']*.25),clamp(c['social_safety']-e['hurt']*.35),clamp(e['joy']+e['excitement']-e['apathy']*.5),clamp(.7-e['anger']*.5-e['irritation']*.3),clamp(.5+e['loneliness']*.2-e['apathy']*.5-e['depressive_tone']*.3),clamp(c['social_need']*(1-e['avoidance'] if 'avoidance' in e else 1)),clamp(withdrawn),clamp(e['irritation']+e['anger']*.4),clamp((e['anger']+e['overwhelm'])*.6*(1-c['inhibition']*.6)))
