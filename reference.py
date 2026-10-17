"""Reference-aware evaluation: target pixels stay in state, references in the rubric."""

from . import boxes, limits, semantics as s


OPENROUTER_MODELS = ("typesafe/jev-1.14", "~typesafe/jev-latest", "typesafe/jev-1.13")
SCORE_DESCRIPTIONS = [
    "The evaluation image does not satisfy the task compared with the reference.",
    "The evaluation image partly satisfies the task, with meaningful differences from the reference.",
    "The evaluation image closely satisfies the task compared with the reference.",
]


def model_choices():
    return {"openrouter": list(OPENROUTER_MODELS), "typesafe": list(limits.TYPESAFE_JEV)}


def prepare(images, reference_images, instructions, task, image_index, reference_index,
            reference_placement, model, provider):
    if provider not in model_choices() or model not in model_choices()[provider]:
        raise ValueError("Reference evaluation requires a verified Jev image model for the selected provider; Luna/Clef and other Decisions models do not support these image placements")
    if task not in ("match", "score") or reference_placement not in ("instructions", "criteria"):
        raise ValueError("Select task match/score and reference_placement instructions/criteria")
    if not isinstance(instructions, str) or not instructions.strip():
        raise ValueError("Describe what to compare with the reference")
    frames, metadata = {}, {}
    for role, batch, index in (("evaluation", images, image_index), ("reference", reference_images, reference_index)):
        frame = boxes.selected_image(batch, index)
        context, upload_size = boxes.context(frame, provider)
        frames[role] = context.parts[-1]
        metadata[role] = {"image_index": index, "batch_size": batch.shape[0],
                          "width": batch.shape[2], "height": batch.shape[1], "upload_size": upload_size,
                          "source_hash": boxes.image_fingerprint(frame)}
    instructions = {"task": instructions.strip(), "evaluate":
                    "Judge only `evaluation_image` in state. The reference image defines the comparison; do not judge the reference image as the target."}
    criteria = ({"true": {"description": "The evaluation image satisfies the task compared with the reference."},
                 "false": {"description": "The evaluation image does not satisfy the task compared with the reference."}}
                if task == "match" else [{"description": text} for text in SCORE_DESCRIPTIONS])
    if reference_placement == "instructions":
        instructions["reference_image"] = frames["reference"]
    else:
        endpoint = criteria["true"] if task == "match" else criteria[-1]
        endpoint["reference_image"] = frames["reference"]
        instructions["reference_location"] = "The reference image is in the positive/highest criterion; compare the evaluation image with that reference."
    question = {"type": "noul" if task == "match" else "score", "instructions": instructions, "criteria": criteria}
    return {"evaluation_image": frames["evaluation"]}, {"evaluation": question}, metadata


def collect(response, question, task, threshold, min_confidence, metadata, reference_placement, provider):
    if not isinstance(response, dict):
        raise ValueError("Reference evaluation response must be a JSON object")
    answer = s._answer(response.get("answers"), "evaluation", "noul" if task == "match" else "score",
                       "reference evaluation", question["criteria"])
    value = answer["noul"] if task == "match" else answer["score"] / (len(question["criteria"]) - 1)
    confidence = None if task == "match" else answer["confidence"]
    reason = "below_threshold" if value < threshold else "low_confidence" if confidence is not None and confidence < min_confidence else None
    details = {**metadata, "task": task, "reference_placement": reference_placement,
               "value": value, "accepted": reason is None, "reason": reason,
               "confidence": confidence, "threshold": threshold, "min_confidence": min_confidence,
               "model": response.get("model"), "provider": provider, "usage": response.get("usage", {}),
               # Score legends may repeat base64 reference images. Keep the
               # default details preview small; response_json retains the wire response.
               "answer": {key: answer[key] for key in ("type", "noul", "score", "confidence", "probabilities") if key in answer}}
    return value, reason is None, details
