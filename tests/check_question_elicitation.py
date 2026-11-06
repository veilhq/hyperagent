"""Checks agent questions (elicitation/create): parse, park, accept/decline/cancel replies, cancel cleanup."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import acp_client  # noqa: E402


class Stub:
    """Just enough of ACPClient for the question flow."""
    _tab_ctx = lambda self: ""  # noqa: E731
    _parse_questions = staticmethod(acp_client.ACPClient._parse_questions)
    _hold_question = acp_client.ACPClient._hold_question
    answer_question = acp_client.ACPClient.answer_question
    _cancel_questions = acp_client.ACPClient._cancel_questions
    _handle_server_request = acp_client.ACPClient._handle_server_request

    def __init__(self):
        self._questions = {}
        self.sent, self.pushed = [], []
        self._cancelled = type("E", (), {"is_set": lambda s: self.cancelled})()
        self.cancelled = False

    def _send(self, msg):
        self.sent.append(msg)

    def _push_js(self, event, data):
        self.pushed.append((event, data))


def opt(value, desc=""):
    return {"const": value, "title": value, "description": desc}


def request(rid, call_id, props, mode="form", message="Pick"):
    return {"id": rid, "method": "elicitation/create", "params": {
        "mode": mode, "sessionId": "s1", "toolCallId": call_id, "message": message,
        "requestedSchema": {"type": "object", "properties": props}}}


SINGLE = {
    "question_0": {"type": "string", "title": "Color", "description": "Which?", "oneOf": [opt("Red", "warm"), opt("Blue")]},
    "question_0_custom": {"type": "string", "title": "Other"},
}
MULTI = {
    "question_0": {"type": "array", "title": "Toppings", "items": {"anyOf": [opt("A"), opt("B"), opt("C")]}},
}


def main():
    c = Stub()

    # Single select: parked (no reply yet), pushed with parsed question and its Other field.
    assert c._hold_question(request(5, "t1", SINGLE)) and not c.sent
    ev, data = c.pushed[-1]
    assert ev == "__acpQuestion" and data["toolCallId"] == "t1" and data["message"] == "Pick"
    assert data["questions"] == [{
        "id": "question_0", "title": "Color", "description": "Which?", "multiSelect": False, "custom": True,
        "options": [{"value": "Red", "title": "Red", "description": "warm"},
                    {"value": "Blue", "title": "Blue", "description": ""}]}]

    # Multi-select without an Other field, plus several questions parsed in numeric order.
    c._hold_question(request(6, "t2", dict(MULTI, **{"question_10": SINGLE["question_0"], "question_2": SINGLE["question_0"]})))
    qs = c.pushed[-1][1]["questions"]
    assert [q["id"] for q in qs] == ["question_0", "question_2", "question_10"]
    assert qs[0]["multiSelect"] is True and not qs[0]["custom"] and len(qs[0]["options"]) == 3
    c._questions.clear()

    # Accept: exact reply, entry popped, card closed; a second answer is unknown.
    c.sent.clear()
    c._hold_question(request(7, "t3", SINGLE))
    answers = {"question_0": {"value": "Red", "custom": "note"}}
    assert c.answer_question("t3", answers, "accept") and not c.answer_question("t3", answers, "accept")
    assert c.sent == [{"jsonrpc": "2.0", "id": 7, "result": {
        "action": "accept", "content": {"question_0": "Red", "question_0_custom": "note"}}}]
    assert c.pushed[-1] == ("__acpQuestionClosed", {"toolCallId": "t3", "action": "accept"}) and not c._questions

    # Accept with a multi-select list and custom-only text.
    c.sent.clear()
    c._hold_question(request(8, "t4", MULTI))
    c.answer_question("t4", {"question_0": {"value": ["A", "C"]}, "question_1": {"value": None, "custom": "mine"}}, "accept")
    assert c.sent[0]["result"] == {"action": "accept", "content": {
        "question_0": ["A", "C"], "question_1_custom": "mine"}}

    # Decline and cancel carry no content.
    for rid, action in ((9, "decline"), (10, "cancel")):
        c.sent.clear()
        c._hold_question(request(rid, "t5", SINGLE))
        assert c.answer_question("t5", {"question_0": {"value": "Red"}}, action)
        assert c.sent == [{"jsonrpc": "2.0", "id": rid, "result": {"action": action}}]

    # Unknown id.
    assert c.answer_question("nope", {}, "accept") is False

    # Cancel cleanup answers every parked question.
    c.sent.clear()
    c._hold_question(request(11, "a", SINGLE))
    c._hold_question(request(12, "b", SINGLE))
    c._cancel_questions()
    assert sorted(m["id"] for m in c.sent) == [11, 12]
    assert all(m["result"] == {"action": "cancel"} for m in c.sent) and not c._questions

    # A cancelled turn replies cancel at once and parks nothing.
    c.sent.clear()
    c.cancelled = True
    assert c._hold_question(request(13, "t6", SINGLE))
    assert c.sent == [{"jsonrpc": "2.0", "id": 13, "result": {"action": "cancel"}}] and not c._questions
    c.cancelled = False

    # Other modes (and a form with no toolCallId) keep the method-not-found reply.
    for msg in (request(14, "t7", {}, mode="url"), request(15, None, SINGLE)):
        c.sent.clear()
        c._handle_server_request(msg)
        assert c.sent[0]["id"] == msg["id"] and c.sent[0]["error"]["code"] == -32601
    assert not c._questions

    # Routed through the request handler, a form request is parked.
    c.sent.clear()
    c._handle_server_request(request(16, "t8", SINGLE))
    assert not c.sent and "t8" in c._questions

    print("question elicitation: ok")


main()
