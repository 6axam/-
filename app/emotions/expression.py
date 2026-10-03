"""Small consistency fence for Luna's transient same-turn expression intent."""
from app.llm.schemas import AffectiveAppraisal, CurrentExpressionIntent


def sanitize_current_expression(intent: CurrentExpressionIntent,
                                appraisal: AffectiveAppraisal) -> CurrentExpressionIntent:
    """Keep a fresh replacement threat from looking like instant reconciliation."""
    a = appraisal.values()
    if (a.get('rejection', 0) < .6 or a.get('replacement_threat', 0) < .6
            or a.get('valence', 0) > -.3 or a.get('repair_attempt', 0) >= .5):
        return intent
    return intent.model_copy(update={
        'warmth_suppression': max(intent.warmth_suppression, .55),
        'impatience': max(intent.impatience, .35),
        'protest_tendency': max(intent.protest_tendency, .4),
        'repair_tendency': min(intent.repair_tendency, .25),
        'reflective_control': min(intent.reflective_control, .55),
    })
