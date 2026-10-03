from app.emotions.affective import AffectiveChemistry, REGULATOR_BASELINES
from app.emotions.profile import EMOTION_NAMES, EmotionProfile, behavior, calculate
from app.emotions.relationship import RelationshipBondState
from app.llm.schemas import AffectiveAppraisal
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

def test_love_and_hostility_coexist_with_low_regulation():
 c=chem(valence=.12,attachment=.92,arousal=.85,energy=.25,inhibition=.2,frustration=.9,threat=.8,stress=.85,social_safety=.2,vulnerability=.9,resentment=.8)
 e=calculate(c,signals=AffectiveAppraisal(social_relevance=1,intensity=1,rejection=1,other_blame=1))
 bond=RelationshipBondState(.92,.83,.9,.9,.12,.8,.91)
 b=behavior(c,e,bond,absence_hours=18)
 assert bond.love_strength>.9 and b.hostility>.45 and e.values()['hurt']>.6
 assert b.warmth_expression<.2 and b.regulation_capacity<.3
 assert b.patience<.4 and b.expression_intensity>.5
 assert b.protest_drive>.3 and b.message_burst_drive>.05
 assert b.caps_drive>.05 and b.profanity_drive>.2

def test_jealousy_requires_explicit_rival_and_absence_creates_longing():
 c=chem(attachment=.9,social_need=.85,vulnerability=.8,arousal=.7,threat=.5)
 neutral=calculate(c,signals=AffectiveAppraisal(social_relevance=1))
 rival=calculate(c,signals=AffectiveAppraisal(social_relevance=1,replacement_threat=1,rival_salience=1))
 bond=RelationshipBondState(.9,.8,.8,.8,.1,.8,.85)
 assert neutral.values()['jealousy']==0
 assert rival.values()['jealousy']>.5
 assert behavior(c,rival,bond).fear_of_loss>behavior(c,neutral,bond).fear_of_loss
 assert behavior(c,neutral,bond,absence_hours=48).longing>.5
 assert behavior(c,neutral,bond).longing==0

def test_apathy_suppresses_contact_despite_absence():
 c=chem(attachment=.9,social_need=.9,energy=.1,reward_sensitivity=.1,arousal=.1)
 e=calculate(c)
 bond=RelationshipBondState(.9,.8,.8,.8,.1,.8,.85)
 b=behavior(c,e,bond,absence_hours=48)
 assert b.longing>.5 and b.initiative_drive<b.longing

def test_moderate_hurt_and_anger_are_not_overregulated():
 c=chem(attachment=.9,arousal=.4,energy=.55,inhibition=.5,frustration=.25,stress=.4,social_safety=.55)
 values=calculate(c).values()
 values.update(hurt=.4,anger=.1,resentment=.2,irritation=.09)
 b=behavior(c,EmotionProfile(values),RelationshipBondState(.9,.8,.8,.8,.1,.8,.88))
 assert b.patience < .55
 assert b.irritability > .25
 assert b.expression_intensity > .25
 assert b.profanity_drive > .18
 assert b.protest_drive > .18
 assert b.regulation_capacity < .5
