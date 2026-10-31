"""Small mood signal shared by initiative timing and its LLM context."""


def mood_initiative_drive(emotions, behavior) -> float:
    """Blend social motivation with mood and apply fatigue/avoidance brakes."""
    if not emotions and not behavior:
        return .5
    initiative = getattr(behavior, "initiative_drive", getattr(behavior, "contact_drive", .5))
    contact = getattr(behavior, "contact_drive", .5)
    protest = getattr(behavior, "protest_drive", .0)
    repair = getattr(behavior, "repair_drive", .0)
    social_need = emotions.get("social_need", .5) if emotions else .5
    curiosity = emotions.get("curiosity", .5) if emotions else .5
    warmth = emotions.get("warmth", .5) if emotions else .5
    fatigue = emotions.get("fatigue", .0) if emotions else 0.
    avoidance = getattr(behavior, "avoidance", 0.)
    drive = .36 * initiative + .22 * contact + .12 * social_need + .10 * curiosity + .08 * warmth + .06 * protest + .06 * repair
    return max(0., min(1., drive * (1 - .55 * avoidance) * (1 - .30 * fatigue)))
