"""Compile semantic fields into Jev questions and resolve answers locally."""

import json
import math
import re
from decimal import Decimal, InvalidOperation


NUMBER_PATTERN = r"(?<![A-Za-z0-9_.])[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
FIELD_TYPES = ("choice", "multi_choice", "boolean", "score", "extract")


def dumps(value):
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)


def _object_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError(f"Invalid JSON number: {value}")


def loads(text, label="JSON"):
    try:
        return json.loads(text, object_pairs_hook=_object_pairs, parse_constant=_invalid_constant)
    except ValueError as error:
        raise ValueError(f"{label}: {error}") from None


def object_json(text, label):
    value = loads(text, label)
    if not isinstance(value, dict):
        raise ValueError(f"{label}: expected a JSON object")
    return value


def pointer(value, path):
    if not path:
        return value
    if not path.startswith("/"):
        raise ValueError(f"JSON Pointer must start with '/': {path}")
    for token in path[1:].split("/"):
        if re.search(r"~(?![01])", token):
            raise ValueError(f"Invalid JSON Pointer escape: {path}")
        key = token.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict) and key in value:
            value = value[key]
        elif isinstance(value, list) and re.fullmatch(r"0|[1-9][0-9]*", key) and int(key) < len(value):
            value = value[int(key)]
        else:
            raise ValueError(f"JSON Pointer not found: {path}")
    return value


def _number(value, label):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"{label}: expected a finite number")
    return value


def _probability(value, label):
    _number(value, label)
    if not 0 <= value <= 1:
        raise ValueError(f"{label}: expected a probability from 0 to 1")
    return value


def validate_schema(schema):
    if not isinstance(schema, dict):
        raise ValueError("Schema must be an object keyed by field ID")
    for field_id, field in schema.items():
        if not isinstance(field_id, str) or not field_id.strip() or not isinstance(field, dict):
            raise ValueError(f"Invalid field: {field_id!r}")
        kind = field.get("type")
        if kind not in FIELD_TYPES:
            raise ValueError(f"{field_id}: unknown field type {kind!r}")
        allowed = {"type", "instructions", "presence"}
        allowed |= {"criteria"} if kind != "extract" else {"source", "pattern", "group"}
        unknown = field.keys() - allowed
        if unknown:
            raise ValueError(f"{field_id}: unknown field settings {sorted(unknown)}")
        if not isinstance(field.get("instructions"), (str, dict, list)):
            raise ValueError(f"{field_id}: instructions must be text, an object, or an array")
        if field.get("presence", "infer") not in ("infer", "explicit"):
            raise ValueError(f"{field_id}: presence must be infer or explicit")
        criteria = field.get("criteria")
        if kind in ("choice", "multi_choice"):
            if not isinstance(criteria, dict) or not criteria or any(not isinstance(k, str) or not k for k in criteria):
                raise ValueError(f"{field_id}: criteria must be a nonempty object of candidate IDs and descriptions")
        elif kind == "score":
            if not isinstance(criteria, list) or len(criteria) < 2:
                raise ValueError(f"{field_id}: score criteria must contain at least two ordered levels")
        elif kind == "boolean" and criteria is not None:
            if not isinstance(criteria, dict) or criteria.keys() - {"true", "false"}:
                raise ValueError(f"{field_id}: boolean criteria use true and false keys")
        elif kind == "extract":
            if not isinstance(field.get("source", ""), str):
                raise ValueError(f"{field_id}: source must be a JSON Pointer")
            pattern = field.get("pattern", NUMBER_PATTERN)
            if not isinstance(pattern, str):
                raise ValueError(f"{field_id}: pattern must be a string")
            try:
                compiled = re.compile(pattern)
            except re.error as error:
                raise ValueError(f"{field_id}: invalid extraction pattern: {error}") from None
            group = field.get("group", 0)
            if type(group) is not int or not 0 <= group <= compiled.groups:
                raise ValueError(f"{field_id}: invalid extraction group {group!r}")
    return schema


def merge_schemas(schemas, schema_json=""):
    merged = {}
    sources = list(schemas)
    if schema_json.strip():
        sources.append(object_json(schema_json, "Schema"))
    for schema in sources:
        validate_schema(schema)
        duplicate = merged.keys() & schema.keys()
        if duplicate:
            raise ValueError(f"Duplicate field IDs: {sorted(duplicate)}")
        merged.update(schema)
    if not merged:
        raise ValueError("Add at least one field to interpret")
    return merged


def make_field(field_id, instructions, kind, presence, criteria="", criteria_format="lines", source="", pattern=NUMBER_PATTERN, group=0):
    field = {"type": kind, "instructions": instructions, "presence": presence}
    if kind in ("choice", "multi_choice", "score"):
        if criteria_format == "json":
            field["criteria"] = loads(criteria, f"{field_id} criteria")
        elif criteria_format == "lines":
            lines = [line.strip() for line in criteria.splitlines() if line.strip()]
            if kind != "score" and len(lines) != len(set(lines)):
                raise ValueError(f"{field_id}: duplicate candidate IDs")
            field["criteria"] = lines if kind == "score" else dict.fromkeys(lines)
        else:
            raise ValueError(f"{field_id}: unknown criteria format")
    elif kind == "boolean" and criteria.strip():
        field["criteria"] = object_json(criteria, f"{field_id} criteria")
    elif kind == "extract":
        field.update(source=source, pattern=pattern, group=group)
    return validate_schema({field_id: field})


def compile_questions(state, schema):
    validate_schema(schema)
    if not isinstance(state, (str, dict, list)):
        raise ValueError("State must be text, an object, or an array")
    questions = {}
    plans = {}
    for index, (field_id, field) in enumerate(schema.items()):
        kind = field["type"]
        qid = f"f{index}"
        plan = {"field": field, "question": qid}
        instruction = field["instructions"]
        criteria = field.get("criteria")
        if kind == "multi_choice":
            plan["options"] = {}
            for option_index, (key, description) in enumerate(criteria.items()):
                option_id = f"{qid}_o{option_index}"
                plan["options"][key] = option_id
                questions[option_id] = {"type": "noul", "instructions": {
                    "task": instruction,
                    "candidate": {"id": key, "description": description},
                    "question": "Does this candidate apply to the state for this task? Evaluate it independently of other candidates.",
                }}
        elif kind == "extract":
            text = pointer(state, field.get("source", ""))
            if not isinstance(text, str):
                raise ValueError(f"{field_id}: extraction source must point to text")
            candidates = {}
            for match in re.finditer(field.get("pattern", NUMBER_PATTERN), text):
                start, end = match.span(field.get("group", 0))
                if start < 0 or start == end:
                    continue
                candidates[f"c{len(candidates)}"] = {
                    "text": text[start:end], "start": start, "end": end,
                    "context": text[max(0, start - 80):min(len(text), end + 80)],
                }
            plan["candidates"] = candidates
            if candidates:
                questions[qid] = {"type": "choice", "instructions": {
                    "task": instruction,
                    "source": field.get("source", ""),
                    "candidates": candidates,
                    "question": "Select the source candidate that answers the task. Select none if no candidate answers it.",
                }, "criteria": {**{key: dumps(value) for key, value in candidates.items()}, "none": "No candidate answers the task."}}
        else:
            questions[qid] = {"type": "noul" if kind == "boolean" else kind, "instructions": instruction}
            if criteria is not None:
                questions[qid]["criteria"] = criteria
        if field.get("presence", "infer") == "explicit" and (kind != "extract" or plan["candidates"]):
            plan["presence_question"] = f"{qid}_present"
            questions[plan["presence_question"]] = {"type": "noul", "instructions": {
                "task": instruction,
                "question": "Does the state explicitly specify information answering this task? Do not infer an unstated preference or invent a value.",
            }}
        plans[field_id] = plan
    return questions, plans


def _answer(answers, qid, kind, field_id, criteria=None):
    answer = answers.get(qid)
    if not isinstance(answer, dict) or answer.get("type") != kind:
        raise ValueError(f"{field_id}: missing or wrong-type answer for {qid}")
    if kind == "noul":
        _probability(answer.get("noul"), f"{field_id} probability")
    else:
        _probability(answer.get("confidence"), f"{field_id} confidence")
        probabilities = answer.get("probabilities")
        if not isinstance(probabilities, dict) or not probabilities:
            raise ValueError(f"{field_id}: missing probability distribution")
        expected = set(criteria) if kind == "choice" else {str(i) for i in range(len(criteria))}
        if set(probabilities) != expected:
            raise ValueError(f"{field_id}: probability keys do not match the question")
        for value in probabilities.values():
            _probability(value, f"{field_id} distribution")
        if kind == "choice" and answer.get("choice") not in criteria:
            raise ValueError(f"{field_id}: returned choice is not a candidate")
        if kind == "score":
            score = _number(answer.get("score"), f"{field_id} score")
            if not 0 <= score <= len(criteria) - 1:
                raise ValueError(f"{field_id}: score is outside its rubric")
    return answer


def collect_judgments(response, plans):
    answers = response.get("answers")
    if not isinstance(answers, dict):
        raise ValueError("Jev response is missing answers")
    fields = {}
    for field_id, plan in plans.items():
        field = plan["field"]
        kind = field["type"]
        result = {"type": kind}
        if "presence_question" in plan:
            result["presence"] = _answer(answers, plan["presence_question"], "noul", field_id)["noul"]
        if kind == "multi_choice":
            result["probabilities"] = {key: _answer(answers, qid, "noul", field_id)["noul"] for key, qid in plan["options"].items()}
        elif kind == "extract" and not plan["candidates"]:
            result["missing"] = True
        else:
            answer_kind = "noul" if kind == "boolean" else "choice" if kind == "extract" else kind
            criteria = field.get("criteria") if kind != "extract" else {**plan["candidates"], "none": None}
            answer = _answer(answers, plan["question"], answer_kind, field_id, criteria)
            result["answer"] = answer
            if kind == "extract":
                result["missing"] = answer["choice"] == "none"
                if not result["missing"]:
                    result["source"] = plan["candidates"][answer["choice"]]
            elif kind == "score":
                result["normalized"] = answer["score"] / (len(criteria) - 1)
        fields[field_id] = result
    return {"fields": fields, "model": response.get("model"), "usage": response.get("usage", {})}


def _convert_extracted(value, conversion, field_id):
    if conversion == "string":
        return value
    if conversion not in ("int", "float"):
        raise ValueError(f"{field_id}: conversion must be string, int, or float")
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise ValueError(f"{field_id}: cannot convert {value!r} to {conversion}") from None
    if not number.is_finite():
        raise ValueError(f"{field_id}: extracted number must be finite")
    if conversion == "int":
        if number != number.to_integral_value():
            raise ValueError(f"{field_id}: {value!r} is not an integer")
        return int(number)
    return _number(float(number), field_id)


def resolve(judgments, bindings):
    if not isinstance(bindings, dict):
        raise ValueError("Bindings must be an object keyed by field ID")
    unknown = bindings.keys() - judgments["fields"].keys()
    if unknown:
        raise ValueError(f"Bindings refer to unknown fields: {sorted(unknown)}")
    results = {}
    values = {}
    for field_id, judgment in judgments["fields"].items():
        binding = bindings.get(field_id, {})
        if not isinstance(binding, dict):
            raise ValueError(f"{field_id}: binding must be an object")
        kind = judgment["type"]
        allowed = {"default", "presence_threshold"}
        allowed |= {"choice": {"values", "min_confidence"}, "multi_choice": {"values", "threshold"},
                    "boolean": {"values", "threshold"}, "score": {"range", "min_confidence"},
                    "extract": {"convert", "min_confidence"}}[kind]
        if binding.keys() - allowed:
            raise ValueError(f"{field_id}: unsupported bindings {sorted(binding.keys() - allowed)}")
        for name in ("threshold", "presence_threshold", "min_confidence"):
            if name in binding:
                _probability(binding[name], f"{field_id} {name}")
        mapping = binding.get("values")
        if mapping is not None and not isinstance(mapping, dict):
            raise ValueError(f"{field_id}: values must be an object")
        reason = None
        answer = judgment.get("answer", {})
        if judgment.get("missing", False):
            reason = "no_match"
        elif judgment.get("presence", 1) < binding.get("presence_threshold", 0.5):
            reason = "not_explicit"
        elif "min_confidence" in binding and answer["confidence"] < binding["min_confidence"]:
            reason = "low_confidence"
        value = None
        if reason is None:
            if kind == "choice":
                value = answer["choice"]
            elif kind == "multi_choice":
                value = [key for key, probability in judgment["probabilities"].items() if probability >= binding.get("threshold", 0.5)]
            elif kind == "boolean":
                value = answer["noul"] >= binding.get("threshold", 0.5)
            elif kind == "score":
                bounds = binding.get("range", [0, 1])
                if not isinstance(bounds, list) or len(bounds) != 2:
                    raise ValueError(f"{field_id}: range must contain two numbers")
                low, high = (_number(v, f"{field_id} range") for v in bounds)
                value = _number(low + judgment["normalized"] * (high - low), field_id)
            else:
                value = _convert_extracted(judgment["source"]["text"], binding.get("convert", "string"), field_id)
            if mapping is not None:
                keys = value if kind == "multi_choice" else [str(value).lower() if kind == "boolean" else value]
                missing = [key for key in keys if key not in mapping]
                if missing:
                    raise ValueError(f"{field_id}: missing value mappings for {missing}")
                value = [mapping[key] for key in keys] if kind == "multi_choice" else mapping[keys[0]]
        status = "resolved"
        if reason is not None:
            status = "default" if "default" in binding else "unresolved"
            value = binding.get("default")
        results[field_id] = {"value": value, "status": status, "reason": reason, "judgment": judgment}
        if status != "unresolved":
            values[field_id] = value
    return {"fields": results}, values


def read_value(result, field_id, path, expected):
    if field_id not in result["fields"]:
        raise ValueError(f"Unknown field: {field_id}")
    field = result["fields"][field_id]
    if field["status"] == "unresolved":
        raise ValueError(f"{field_id}: value is unresolved; set a default or branch with Jev Inspect Field")
    value = pointer(field["value"], path)
    if expected is float and type(value) in (int, float):
        return _number(float(value), field_id)
    if expected is int and type(value) is float and math.isfinite(value) and value.is_integer():
        return int(value)
    if type(value) is not expected:
        raise ValueError(f"{field_id}{path}: expected {expected.__name__}, got {type(value).__name__}")
    return value


def numeric_judgment(judgments, field_id):
    if field_id not in judgments["fields"]:
        raise ValueError(f"Unknown field: {field_id}")
    field = judgments["fields"][field_id]
    if field.get("presence", 1) < 0.5:
        raise ValueError(f"{field_id}: no explicit value to aggregate")
    if field["type"] == "score":
        return field["normalized"]
    if field["type"] == "boolean":
        return field["answer"]["noul"]
    raise ValueError(f"{field_id}: ranking and aggregation require score or boolean fields")


def rank(judgments, field_ids):
    if not field_ids or len(field_ids) != len(set(field_ids)):
        raise ValueError("Rank requires a nonempty list of unique field IDs")
    return sorted(({"id": key, "value": numeric_judgment(judgments, key)} for key in field_ids), key=lambda row: row["value"], reverse=True)


def weighted_score(judgments, weights):
    if not isinstance(weights, dict) or not weights:
        raise ValueError("Weights must be a nonempty object keyed by field ID")
    rows = [{"id": key, "value": numeric_judgment(judgments, key), "weight": _number(weight, f"{key} weight")} for key, weight in weights.items()]
    total = sum(row["weight"] for row in rows)
    if total == 0:
        raise ValueError("Weights must not sum to zero")
    value = sum(row["weight"] * row["value"] for row in rows) / total
    return _number(value, "Weighted score"), rows
