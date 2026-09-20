"""Rank descriptions, then verify a short list against complete candidate content."""

from . import api, semantics as s


GATES = {
    "work": "Does this request ask for concrete work or an artifact with task-specific requirements, rather than only general conversation?",
    "procedure": "Would relevant documented methods, tool instructions, or creative guidelines materially help satisfy this request?",
    "general": "Can this request be fully satisfied from general knowledge alone, without any specialized procedure or creative guidance?",
}

APPLICABILITY = [
    "The candidate contributes nothing useful to this request, or its requirements conflict with the requested work.",
    "The candidate helps with an optional detail; consult it only for that limited part of the work.",
    "The candidate supplies a useful method for one requested part; apply it normally within that scope.",
    "The candidate guides a major part of the requested work and should shape the main approach for that part.",
    "The candidate provides the central method or creative direction explicitly needed for the requested outcome.",
]


async def suggest(state, instructions, candidates, model, provider, api_key,
                  shortlist_size=3, max_selections=1, gate_threshold=0.3, threshold=0.5,
                  score_applicability=False):
    if not isinstance(instructions, str) or not instructions.strip():
        raise ValueError("Instructions must not be empty")
    for name, value in (("shortlist_size", shortlist_size), ("max_selections", max_selections)):
        if type(value) is not int or value < 1:
            raise ValueError(f"{name} must be a positive integer")
    s._probability(gate_threshold, "gate_threshold")
    s._probability(threshold, "threshold")
    records = {f"c{i}": candidate for i, candidate in enumerate(candidates)}
    details = {"status": "none", "reason": "empty_catalog", "selected": [], "ranking": [], "shortlist": [], "fits": {}}
    responses = {}
    if not records:
        return [], details, responses

    criteria = {key: item["description"] for key, item in records.items()}
    questions = {"which": {"type": "choice", "instructions": instructions, "criteria": criteria}}
    questions.update({f"gate_{key}": {"type": "noul", "instructions": text} for key, text in GATES.items()})
    wide = await api.evaluate(state, questions, model, provider=provider, api_key=api_key)
    responses["rank"] = wide
    answers = wide.get("answers", {})
    ranked_answer = s._answer(answers, "which", "choice", "ranking", criteria)
    gates = {key: s._answer(answers, f"gate_{key}", "noul", f"gate {key}")["noul"] for key in GATES}
    gate = (gates["work"] + gates["procedure"] + 1 - gates["general"]) / 3
    ranked = sorted(records, key=lambda key: -ranked_answer["probabilities"][key])
    details.update(gate=gate, gate_answers=gates,
                   ranking=[{"id": key, "value": records[key]["value"], "probability": ranked_answer["probabilities"][key]} for key in ranked])
    if gate < gate_threshold:
        details["reason"] = "skill_not_needed"
        return [], details, responses

    shortlist = ranked[:shortlist_size]
    details["shortlist"] = shortlist
    criteria = {key: {"description": records[key]["description"], "content": records[key]["content"]} for key in shortlist}
    questions = {}
    if not score_applicability:
        questions["which"] = {"type": "choice", "instructions": {
            "task": instructions,
            "comparison": "Compare what the candidates actually do using their full content. Pick the best fit for this request.",
        }, "criteria": criteria}
    for key in shortlist:
        questions[f"fits_{key}"] = {"type": "noul", "instructions": {
            "task": instructions, "candidate": criteria[key],
            "question": "Does this candidate directly help with the specific requested work? Shared topic or vocabulary alone is insufficient. Judge this candidate independently; it is acceptable for all candidates to be unsuitable.",
        }}
        if score_applicability:
            questions[f"applicability_{key}"] = {"type": "score", "instructions": {
                "task": instructions, "candidate": criteria[key],
                "question": "How much of the requested work should this candidate guide? Rate its role in this request independently. Several candidates may each be central to different requested parts. Topic overlap alone does not establish usefulness; unmet prerequisites or conflicts with the request mean it should not be applied.",
            }, "criteria": APPLICABILITY}
    narrow = await api.evaluate(state, questions, model, provider=provider, api_key=api_key)
    responses["verify"] = narrow
    answers = narrow.get("answers", {})
    fits = {key: s._answer(answers, f"fits_{key}", "noul", f"candidate {key}")["noul"] for key in shortlist}
    details["fits"] = fits
    if score_applicability:
        applicability = {
            key: s._answer(answers, f"applicability_{key}", "score", f"candidate {key}", APPLICABILITY)["score"] / (len(APPLICABILITY) - 1)
            for key in shortlist
        }
        details["applicability"] = applicability
        eligible = [key for key in records if key in applicability and fits[key] >= threshold and applicability[key] > 0]
        selected = sorted(eligible, key=lambda key: -applicability[key])[:max_selections]
    else:
        choice = s._answer(answers, "which", "choice", "verification", criteria)
        # Choice compares alternatives; each independent fit check must also pass.
        ordered = sorted(shortlist, key=lambda key: -choice["probabilities"][key])
        winner = choice["choice"]
        ordered.remove(winner)
        ordered.insert(0, winner)
        selected = [key for key in ordered if fits[key] >= threshold][:max_selections]
        if max_selections == 1 and fits[winner] < threshold:
            selected = []
    values = [records[key]["value"] for key in selected]
    details.update(status="selected" if selected else "none", reason=None if selected else "no_suitable_candidate", selected=selected)
    return values, details, responses
