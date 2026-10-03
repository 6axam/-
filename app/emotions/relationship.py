"""Slow, chat-scoped relationship history. No model can assign these values."""
from __future__ import annotations

import json
from dataclasses import dataclass, replace

from app.emotions.affective import clamp


@dataclass(frozen=True)
class RelationshipBondState:
    bond_strength: float = .45
    relational_trust: float = .50
    familiarity: float = .35
    care_investment: float = .40
    rupture_load: float = .05
    relationship_security: float = .47
    love_strength: float = .43
    meaningful_interactions: int = 0

    def values(self):
        return {key: getattr(self, key) for key in (
            'bond_strength', 'relational_trust', 'familiarity', 'care_investment',
            'rupture_load', 'relationship_security', 'love_strength')}

    @property
    def attachment_target(self):
        return clamp(.08 + .74 * self.bond_strength + .18 * self.care_investment)

    @classmethod
    def from_row(cls, row):
        return cls(*(row[key] for key in cls.__dataclass_fields__))


def _signal(appraisal):
    values = appraisal.values() if hasattr(appraisal, 'values') else dict(appraisal)
    return {key: float(value or 0) for key, value in values.items()}


def evolve_relationship(state: RelationshipBondState, appraisal, *, meaningful=True, test_mode=False):
    """One bounded accepted event. Acute pain cannot erase a durable bond."""
    a = _signal(appraisal)
    relevance = a.get('social_relevance', 0)
    negative = max(a.get('rejection', 0), a.get('dismissal', 0),
                   a.get('betrayal', 0), a.get('replacement_threat', 0)) * relevance
    positive = max(a.get('care', 0), a.get('warmth', 0), a.get('repair_attempt', 0)) * relevance
    repair = a.get('repair_attempt', 0) * relevance
    if not meaningful or relevance < .25 or (negative < .35 and positive < .35):
        return state
    scale = .12 if test_mode else 1.0
    rupture = clamp(state.rupture_load + scale * (.027 * negative - .014 * repair - .003 * positive))
    trust = clamp(state.relational_trust + scale * (.012 * positive + .006 * repair - .024 * negative * (1 + .4 * state.rupture_load)))
    care = clamp(state.care_investment + scale * (.008 * positive - .003 * negative))
    familiarity = clamp(state.familiarity + .002 * scale)
    # Long established bonds resist isolated shocks; accumulated rupture allows
    # sustained damage to matter. Formation is deliberately slower than repair.
    bond_delta = scale * (.004 * positive * (1 - state.bond_strength)
                          - .010 * negative * max(0, rupture - .12))
    bond = clamp(state.bond_strength + bond_delta)
    security = clamp(.55 * trust + .25 * bond + .20 * (1 - rupture))
    target_love = clamp(.45 * bond + .20 * care + .15 * familiarity +
                        .12 * trust + .08 * security - .16 * max(0, rupture - .35))
    # Different entering/leaving rates supply hysteresis without a love toggle.
    rate = .025 if target_love > state.love_strength else .012
    love = clamp(state.love_strength + (target_love - state.love_strength) * rate)
    return RelationshipBondState(bond, trust, familiarity, care, rupture,
                                 security, love, state.meaningful_interactions + 1)


class RelationshipBondManager:
    def __init__(self, db):
        self.db = db

    async def get(self, chat_id):
        row = await self.db.fetchone('SELECT * FROM relationship_bonds WHERE chat_id=?', (chat_id,))
        if row:
            return RelationshipBondState.from_row(row)
        # Existing installations have no reliable relationship history. Use a
        # conservative one-time approximation from persisted affect, never a
        # precise claim about past love or a recurring baseline attractor.
        affect = await self.db.fetchone('SELECT regulators_json FROM affective_states WHERE chat_id=?', (chat_id,))
        state = RelationshipBondState()
        if affect:
            values = json.loads(affect['regulators_json'])
            attachment = clamp(values.get('attachment', .5))
            safety = clamp(values.get('social_safety', .5))
            state = replace(state, bond_strength=clamp(.25 + attachment * .45),
                            relational_trust=clamp(.20 + safety * .55),
                            relationship_security=clamp(.25 + safety * .5))
            state = replace(state, love_strength=clamp(.35 * state.bond_strength +
                            .20 * state.care_investment + .15 * state.familiarity +
                            .15 * state.relational_trust + .15 * state.relationship_security))
        await self._save(chat_id, state)
        return state

    async def _save(self, chat_id, state):
        await self.db.execute(
            'INSERT INTO relationship_bonds(chat_id,bond_strength,relational_trust,familiarity,care_investment,rupture_load,relationship_security,love_strength,meaningful_interactions) '
            'VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(chat_id) DO UPDATE SET '
            'bond_strength=excluded.bond_strength,relational_trust=excluded.relational_trust,familiarity=excluded.familiarity,care_investment=excluded.care_investment,'
            'rupture_load=excluded.rupture_load,relationship_security=excluded.relationship_security,love_strength=excluded.love_strength,'
            'meaningful_interactions=excluded.meaningful_interactions,updated_at=CURRENT_TIMESTAMP',
            (chat_id, *state.values().values(), state.meaningful_interactions))

    async def apply(self, chat_id, appraisal, *, meaningful=True, test_mode=False):
        before = await self.get(chat_id)
        after = evolve_relationship(before, appraisal, meaningful=meaningful, test_mode=test_mode)
        if after != before:
            await self._save(chat_id, after)
        return before, after


def dominant_motive(behavior, emotions):
    """Select one invitation to the existing initiative model; no extra call."""
    b = behavior.values()
    e = emotions.values()
    scores = {
        'care': b['warmth_expression'] * .7 + b['contact_drive'] * .3,
        'curiosity': e['curiosity'],
        'longing': b['longing'],
        'protest': b['protest_drive'],
        'jealousy': e['jealousy'],
        'repair': b['repair_drive'],
        'sharing': e['interest'] * .7 + b['response_energy'] * .3,
        'boredom': e['boredom'],
        'playfulness': b['playfulness'],
    }
    return max(scores, key=scores.get)


def relationship_context(bond, behavior, *, motive=None):
    values = bond.values()
    fields = (
        ('bond', values['bond_strength']), ('trust', values['relational_trust']),
        ('security', values['relationship_security']), ('love_strength', values['love_strength']),
        ('rupture', values['rupture_load']), ('longing', behavior.longing),
        ('fear_of_loss', behavior.fear_of_loss), ('protest_drive', behavior.protest_drive),
        ('repair_drive', behavior.repair_drive), ('contact_drive', behavior.contact_drive),
        ('rumination_drive', behavior.rumination_drive),
        ('regulation_capacity', behavior.regulation_capacity),
        ('message_burst_drive', behavior.message_burst_drive),
        ('caps_drive', behavior.caps_drive), ('profanity_drive', behavior.profanity_drive),
    )
    return ('RELATIONSHIP STATE\n' + '; '.join(f'{name}={value:.2f}' for name, value in fields)
            + (f'; dominant_motive={motive}' if motive else '')
            + '\nPrivate learned continuity. Love does not require a love declaration. Current affect and event control expression; never quote scores.')
