from app.actions.models import Action, ActionType
from app.conversation.message_splitter import MessageSplitter


def splitter(): return MessageSplitter(target_chars=70, min_chars=25, max_parts=4)


def test_short_message_is_not_split():
    assert splitter().split_text("ага, прикольно") == ["ага, прикольно"]


def test_conversational_sentences_and_dash_split_naturally():
    text = "Наверное, мне больше всего заходит электронщина — что-нибудь атмосферное, с красивыми синтами, но не совсем фоновое. Хотя вкусы у меня пока ещё формируются, так что могу внезапно залипнуть вообще на что-то другое)"
    assert splitter().split_text(text) == [
        "Наверное, мне больше всего заходит электронщина",
        "что-нибудь атмосферное, с красивыми синтами, но не совсем фоновое.",
        "Хотя вкусы у меня пока ещё формируются, так что могу внезапно залипнуть вообще на что-то другое)",
    ]


def test_transition_word_creates_preferred_boundary():
    text = "Мне в целом нравится эта идея, она выглядит довольно живой и не слишком перегруженной, хотя надо ещё посмотреть, как оно поведёт себя на реальном проекте"
    parts = splitter().split_text(text)
    assert len(parts) == 2
    assert parts[1].lower().startswith("хотя")


def test_technical_content_is_never_split():
    long_code = "```python\nprint('this must stay intact even though it is very long and contains several sentences. Really long.')\n```"
    assert splitter().split_text(long_code) == [long_code]
    command = "git log --oneline --all --decorate --graph --max-count=100 && python -m pytest -q"
    assert splitter().split_text(command) == [command]
    url = "https://example.com/a/very/long/url?with=many&parameters=that&must=remain&whole=true"
    assert splitter().split_text(url) == [url]


def test_existing_multiple_llm_text_actions_stay_separate():
    actions = [Action(type=ActionType.text, text="первая"), Action(type=ActionType.text, text="вторая")]
    assert splitter().split_actions(actions) == actions


def test_split_text_preserves_delivery_metadata_on_every_part():
    original = Action(
        type=ActionType.text,
        text=("Эта длинная мысль должна остаться ответом на конкретное сообщение. "
              "Хотя она разбивается на несколько отдельных пузырей."),
        reply_to_message_id=42,
        automatic=True,
        cancelable=False,
        priority=7,
    )

    parts = splitter().split_actions([original])

    assert len(parts) > 1
    assert all(part.reply_to_message_id == 42 for part in parts)
    assert all(part.automatic is True and part.cancelable is False and part.priority == 7 for part in parts)
