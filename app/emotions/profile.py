"""Derived emotional activations and behaviour from affective chemistry."""
import math
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
 hostility:float; longing:float; fear_of_loss:float; protest_drive:float; repair_drive:float; rumination_drive:float; regulation_capacity:float; contact_drive:float; message_burst_drive:float; caps_drive:float; profanity_drive:float; affective_volatility:float
 def values(self): return self.__dict__.copy()
def _mix(previous, target, name):
 rate=.18 if name in SLOW else (.62 if name in FAST else .36)
 return clamp(previous.get(name,0)* (1-rate) + target*rate)


def _soft_bound(value, *, low_knee=.22, high_knee=.72, floor=.015, ceiling=.985):
 """Preserve midrange resolution while approaching extremes asymptotically."""
 value=float(value)
 if value < low_knee:
  return floor+(low_knee-floor)*math.exp((value-low_knee)/.30)
 if value > high_knee:
  return high_knee+(ceiling-high_knee)*(1-math.exp(-(value-high_knee)/.42))
 return value


def _soft_ceiling(value, *, high_knee=.72, ceiling=.985):
 """Leave quiet channels quiet while retaining headroom at high activation."""
 value=max(0.,float(value))
 if value <= high_knee:
  return value
 return high_knee+(ceiling-high_knee)*(1-math.exp(-(value-high_knee)/.42))
def calculate(chemistry, previous=None, signals=None):
 c=chemistry.values(); first=previous is None; p=(previous or {}).copy(); neg=1-c['valence']; a=c['arousal']; attachment=c['attachment']; hurt=clamp((neg+c['vulnerability']+attachment+c['resentment'])/4)
 targets={'joy':c['valence']*c['reward_sensitivity'],'contentment':c['valence']*(1-a*.3),'excitement':c['valence']*a,'amusement':c['valence']*c['reward_sensitivity']*.6,'relief':max(0,p.get('anxiety',0)-c['threat'])*.8,'hope':c['valence']*c['novelty_drive'],'gratitude':c['valence']*c['social_safety']*.5,'pride':c['self_worth']*c['valence'],'affection':attachment*c['social_safety']*(.55+.45*c['valence']),'tenderness':attachment*c['vulnerability']*.7,'trust':c['social_safety']*attachment,'closeness':attachment*c['social_safety'],'longing':attachment*c['social_need'],'loneliness':c['social_need']*neg,'curiosity':c['novelty_drive']*c['reward_sensitivity'],'interest':c['novelty_drive']*c['valence'],'anticipation':c['novelty_drive']*a,'surprise':a*c['novelty_drive']*.5,'boredom':(1-c['novelty_drive'])*(1-a),'sadness':neg*c['grief_load'],'melancholy':neg*c['grief_load']*.7,'grief':c['grief_load'],'disappointment':neg*c['reward_sensitivity'],'apathy':(1-c['energy'])*(1-c['reward_sensitivity'])*(1-a),'depressive_tone':(neg+(1-c['energy'])+(1-c['reward_sensitivity'])+c['grief_load']+c['stress'])/5,'irritation':c['frustration']*a,'frustration':c['frustration'],'anger':c['frustration']*a*c['threat'],'hurt':hurt,'resentment':c['resentment'],'jealousy':attachment*c['threat']*.4,'envy':c['threat']*(1-c['self_worth'])*.3,'disgust':c['threat']*c['frustration']*.4,'anxiety':c['stress']*c['threat']*max(.4,a),'fear':c['threat']*a,'shame':(1-c['self_worth'])*c['vulnerability']*.4,'guilt':c['frustration']*(1-c['self_worth'])*.2,'embarrassment':c['vulnerability']*(1-c['social_safety'])*.3,'nostalgia':attachment*c['grief_load']*.3,'overwhelm':c['stress']*a,'defensiveness':c['threat']*(1-c['social_safety']),'regret':p.get('anger',0)*(1-a)*attachment*(1-c['inhibition'])}
 s=signals.values() if hasattr(signals,'values') else (signals or {})
 relevance=s.get('social_relevance') or 0
 volatility=clamp(.3*c['stress']+.25*(1-c['energy'])+.25*c['arousal']+.2*c['vulnerability'])
 intensity=(s.get('intensity') if s.get('intensity') is not None else .5)*(1+.5*volatility)
 targets['hurt']=clamp(targets['hurt']+relevance*intensity*(.55*(s.get('rejection') or 0)+.35*(s.get('dismissal') or 0)+.30*(s.get('replacement_threat') or 0)))
 targets['anger']=clamp(.45*c['frustration']+.25*c['arousal']+.15*c['threat']+relevance*intensity*(.70*(s.get('rejection') or 0)+.45*(s.get('other_blame') or 0)+.35*(s.get('dismissal') or 0)))
 targets['anger']*=1-.75*(s.get('repair_attempt') or 0)
 targets['irritation']=clamp(targets['irritation']+relevance*intensity*.6*(s.get('rejection') or 0))
 values={n:(clamp(targets.get(n,0)) if first else _mix(p,targets.get(n,0),n)) for n in EMOTION_NAMES}
 # Jealousy needs an explicit relational comparison/replacement signal.
 trigger=min(s.get('replacement_threat') or 0,s.get('rival_salience') or 0)*(s.get('social_relevance') or 0)
 jealousy=clamp(c['attachment']*trigger*(.55+.45*c['vulnerability']))
 values['jealousy']=jealousy if first else _mix(p,jealousy,'jealousy')
 return EmotionProfile(values)

def behavior(chemistry, emotions, bond=None, *, absence_hours=0, night=False):
 c=chemistry.values(); e=emotions.values()
 bond_strength=bond.bond_strength if bond else c['attachment']
 love=bond.love_strength if bond else e['affection']
 rupture=bond.rupture_load if bond else 0
 arousal=c['arousal']; hurt=e['hurt']; anger=e['anger']; apathy=e['apathy']
 absence=clamp(absence_hours/36)
 longing=clamp((.35*bond_strength+.30*c['attachment']+.25*c['social_need']+.10*e['affection'])*absence)
 fear=clamp((.45*e['jealousy']+.30*c['threat']+.25*hurt)*bond_strength*(.6+.4*c['vulnerability']))
 hostility=_soft_ceiling(.52*anger+.26*e['resentment']+.12*e['disgust']+.10*hurt+.18*c['frustration'])
 protest=_soft_ceiling((.40*hurt+.30*anger+.20*e['resentment']+.20*c['frustration']+.20*c['social_need']+.15*fear)*(.4+.6*arousal)*(.4+.6*bond_strength))
 repair=clamp((.45*e['regret']+.20*hurt+.20*e['closeness']+.15*rupture)*bond_strength*(1-.7*anger))
 rumination=_soft_ceiling((.35*hurt+.30*e['resentment']+.20*rupture+.15*fear)*c['attachment']*(.6+.4*arousal))
 volatility=clamp(.30*c['stress']+.23*(1-c['energy'])+.22*arousal+.15*c['vulnerability']+.10*rupture+(.03 if night else 0))
 regulation=_soft_bound(.27*c['energy']+.22*c['social_safety']+.20*c['inhibition']+.15*(1-c['stress'])+.16*(1-arousal)-.25*hurt-.27*anger-.15*e['overwhelm']-.10*e['resentment']-.12*c['frustration']-(.05 if night else 0))
 avoidance=_soft_ceiling(.45*hurt+.30*(1-c['energy'])+.25*(1-c['social_safety']))
 overload=max(0,e['overwhelm']-.62)+.6*max(0,.28-c['energy'])
 expression=_soft_ceiling(.12+.75*anger+.35*e['irritation']+.30*e['overwhelm']+.20*arousal+.55*max(0,hurt-.25)+.30*e['resentment']+.35*c['frustration']-.25*c['inhibition']-.32*apathy-.12*(1-c['energy'])-.28*overload)
 patience=_soft_bound(.85-.72*anger-.35*e['irritation']-.36*hurt-.18*c['stress']-.25*e['resentment']-.28*c['frustration'])
 fluency=clamp(.55+.38*c['energy']-.55*apathy-.35*hurt-.15*e['overwhelm'])
 warmth=clamp(.45*e['affection']+.35*love+.20*c['social_safety']-.55*hurt-.35*anger-.25*avoidance)
 contact=clamp(.25*c['social_need']+.30*longing+.15*bond_strength+.15*repair+.15*e['curiosity']-.35*avoidance-.35*apathy-.12*(1-c['energy']))
 burst=_soft_ceiling((.40*protest+.25*e['excitement']+.20*fear+.15*longing)*arousal*(.4+.6*expression)*(1-.65*regulation)+(.02 if night and longing > .5 else 0))
 caps=_soft_ceiling(expression*arousal*(1-c['inhibition'])*(.45+.55*anger))
 profanity=_soft_ceiling((.55*anger+.25*e['irritation']+.22*expression+.22*hurt+.20*c['frustration']+.12*e['resentment'])*(1-.50*regulation))
 initiative=clamp(.18+.75*contact-.35*apathy-.25*avoidance)
 return AffectiveBehaviorProfile(
  response_energy=clamp(c['energy']*(1-apathy*.6)), verbal_fluency=fluency,
  warmth_expression=warmth, openness=clamp(c['social_safety']-.45*hurt),
  playfulness=clamp(e['joy']+e['excitement']-.5*apathy), patience=patience,
  initiative_drive=initiative, social_seeking=clamp(c['social_need']*(1-avoidance)),
  avoidance=avoidance, irritability=_soft_ceiling(.1+.45*e['irritation']+.55*anger+.35*c['frustration']+.30*max(0,hurt-.25)+.25*e['resentment']),
  expression_intensity=expression, hostility=hostility, longing=longing,
  fear_of_loss=fear, protest_drive=protest, repair_drive=repair,
  rumination_drive=rumination, regulation_capacity=regulation,
  contact_drive=contact, message_burst_drive=burst, caps_drive=caps,
  profanity_drive=profanity, affective_volatility=volatility)
