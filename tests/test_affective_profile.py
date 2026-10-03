from app.emotions.affective import AffectiveChemistry, REGULATOR_BASELINES
from app.emotions.profile import EMOTION_NAMES, behavior, calculate
def chem(**changes):
 v=dict(REGULATOR_BASELINES); v.update(changes); return AffectiveChemistry(v)
def test_spectrum_is_bounded_and_affection_hurt_can_coexist():
 e=calculate(chem(valence=.2,attachment=.95,vulnerability=.9,resentment=.8,frustration=.8,arousal=.9,threat=.7))
 assert len(EMOTION_NAMES)==42 and e.values()['affection']>.2 and e.values()['hurt']>.4
 assert all(0<=v<=1 for v in e.values().values())
def test_apathy_and_inhibition_behavior():
 c=chem(energy=.1,reward_sensitivity=.1,arousal=.1,inhibition=.9,frustration=.9,threat=.9)
 e=calculate(c); b=behavior(c,e)
 assert e.values()['apathy']>.25 and b.verbal_fluency<.7 and b.expression_intensity<.5
